"""Fase 3 - Fine-tune ligero con LoRA del checkpoint DTI sobre el dataset
curado de dianas bacterianas (data/processed/).

Diseno v1 (acordado y registrado en docs/decisions.md, seccion Fase 3):

- QSAR de POTENCIA FENOTIPICA (SMILES -> pMIC), no binding especifico. El
  campo "target" que exige el checkpoint se rellena con una secuencia real
  de GyrA fija por patogeno, como ANCLA DE CONTEXTO DE ORGANISMO.
  IMPORTANTE: usar GyrA es un requisito de arquitectura del checkpoint, NO
  implica que las predicciones sean especificas de union a GyrA ni a
  fluoroquinolonas (la clase de antibiotico que si actua sobre GyrA). El
  nombre "GyrA" aqui es solo el ancla de organismo.
- Regresion MSE sobre las filas EXACTAS (relation "=") + hinge "Tobit-lite"
  SIMETRICO sobre las censuradas con cota:
    * ">"/">=" (cota superior de pMIC b): penaliza max(0, pred - b)^2
    * "<"/"<=" (cota inferior de pMIC b): penaliza max(0, b - pred)^2
  Las filas inhibition-only (sin pMIC ni cota) NO entran aqui; se reservan
  para calibracion en Fase 7.
- Entrenamiento ENCODER-ONLY (la prediccion DTI usa forward_encoder_only, el
  decoder no interviene) -> LoRA solo sobre q,v del encoder T5.
- Split por inchikey (grupo), estratificado por is_hit, separado por patogeno.
- PILOTO DE TIMING PRIMERO: `--pilot` mide s/paso y proyecta el coste de una
  epoca antes de lanzar cualquier run largo (estamos en CPU).

Las 66 filas de verification_binding_<patogeno>.csv (Ki/Kd reales) NO se
tocan aqui: reservadas para el chequeo cualitativo de Fase 7.

Uso:
    uv run python -m training.lora_finetune --pilot          # medir timing
    uv run python -m training.lora_finetune --max-steps 2000 # run acotado
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import torch
from peft import LoraConfig, get_peft_model

from app.config import settings
from app.foundation.dti_model import (
    NORM_Y_MEAN,
    NORM_Y_STD,
    _build_encoder_input,
    load_model,
)
from mammal.keys import SCALARS_PREDICTION_HEAD_LOGITS

# --- Anclas de contexto de organismo (GyrA), trazables por accession UniProt.
# Ver nota en el docstring / decisions.md: ancla de arquitectura, NO afirmacion
# de union especifica a GyrA/fluoroquinolonas.
GYRA_ANCHORS = {
    "Klebsiella pneumoniae": {
        "accession": "A0A0H3H0Y6",  # cepa HS11286, gyrA KPHS_37060, 877 aa
        "md5": "7dfc605d5f68774ddf990263ffb5433b",
    },
    "Acinetobacter baumannii": {
        "accession": "A0A0D5YFF2",  # gyrA ABUW_0960, 904 aa
        "md5": "f6be4367e90b430a8843e7de8f29f7c0",
    },
}
_UNIPROT_FASTA_URL = "https://rest.uniprot.org/uniprotkb/{accession}.fasta"

# Direccion de censura (derivada de la columna relation del dataset curado).
_DIR_EQ, _DIR_UPPER, _DIR_LOWER = 0, 1, 2
_RELATION_TO_DIR = {
    "=": _DIR_EQ,
    "~": _DIR_EQ,  # 1 sola fila en todo el dataset; se trata como exacta
    ">": _DIR_UPPER,
    ">=": _DIR_UPPER,
    "<": _DIR_LOWER,
    "<=": _DIR_LOWER,
}

# target = q,v de la atencion del encoder T5 (regex: excluye el decoder, que no
# se usa en forward_encoder_only).
_LORA_TARGET_REGEX = r"t5_model\.encoder\.block\..*\.(q|v)$"
# Solo la cabeza escalar (hidden->1, ~0M) entra como entrenable. `encoder_head`
# (106M, proyeccion a vocabulario para token-classification) NO se entrena: su
# salida no interviene en la loss de regresion; incluirla eran 106M de params
# inutiles machacando la CPU y desvirtuando el "LoRA ligero".
_LORA_MODULES_TO_SAVE = ["scalars_prediction_head"]

_OUTPUT_DIR = Path("training/output")


@dataclass
class TrainConfig:
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    lr: float = 1e-4
    batch_size: int = 1  # en la GTX 1070 (8GB) solo entra batch 1 a seq 1512
    grad_accum: int = 8  # batch efectivo = batch_size * grad_accum
    max_steps: int | None = None  # en pasos de optimizador (no micro-batches)
    epochs: int = 1
    test_frac: float = 0.15
    seed: int = 42
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    limit_rows: int | None = None  # subset para pruebas rapidas
    pilot_steps: int = 60
    min_test_hits: int = 100  # assert por patogeno tras el split
    eval_subset: int = 256  # filas de test para eval periodica (coste CPU)
    eval_every: int = 200  # pasos entre evals
    ckpt_every: int = 500  # pasos entre checkpoints intermedios


# --------------------------------------------------------------------------- #
# Anclas GyrA
# --------------------------------------------------------------------------- #
def _fetch_gyra_sequence(pathogen: str) -> str:
    """Descarga (y cachea) la secuencia GyrA del patogeno y verifica su md5
    contra el valor registrado, para que un cambio silencioso de la fuente
    falle alto y claro en vez de entrenar con una secuencia distinta."""
    anchor = GYRA_ANCHORS[pathogen]
    cache_dir = settings.data_raw_dir / ".cache" / "anchors"
    cache_path = cache_dir / f"{anchor['accession']}.fasta"

    if cache_path.exists():
        fasta = cache_path.read_text()
    else:
        resp = requests.get(
            _UNIPROT_FASTA_URL.format(accession=anchor["accession"]), timeout=60
        )
        resp.raise_for_status()
        fasta = resp.text
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(fasta)

    seq = "".join(line for line in fasta.splitlines() if not line.startswith(">"))
    md5 = hashlib.md5(seq.encode()).hexdigest()
    if md5 != anchor["md5"]:
        raise ValueError(
            f"md5 de GyrA para '{pathogen}' ({anchor['accession']}) no coincide: "
            f"esperado {anchor['md5']}, obtenido {md5}. La fuente ha cambiado; "
            "revisar antes de entrenar."
        )
    return seq


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def _assert_censor_direction(df: pd.DataFrame) -> None:
    """Verifica EMPIRICAMENTE que el mapeo de censura no esta invertido:
    las filas ">" deben tener pX mediana MAS BAJA que "=", y las "<" MAS
    ALTA (MIC mayor -> menos potente -> pMIC menor, y viceversa). Un mapeo
    invertido entrenaria el hinge al reves en ~24K filas sin que la loss lo
    delate, asi que se comprueba sobre los datos reales, no por lectura."""
    for pathogen, sub in df.groupby("pathogen"):
        med_eq = sub[sub["relation"] == "="]["pX"].median()
        med_gt = sub[sub["relation"].isin([">", ">="])]["pX"].median()
        med_lt = sub[sub["relation"].isin(["<", "<="])]["pX"].median()
        if pd.isna(med_gt) or pd.isna(med_lt) or pd.isna(med_eq):
            continue
        if not (med_gt < med_eq < med_lt):
            raise AssertionError(
                f"[{pathogen}] direccion de censura inesperada: median pX "
                f'">"={med_gt:.3f} "="={med_eq:.3f} "<"={med_lt:.3f} '
                "(se esperaba >  <  =  <  <). Revisar _RELATION_TO_DIR."
            )


def _load_trainable_rows(cfg: TrainConfig) -> pd.DataFrame:
    """Carga las filas entrenables (exactas + censuradas con cota) de todos
    los patogenos configurados. Descarta inhibition-only (sin pMIC ni cota)."""
    frames = []
    for pathogen in settings.pathogens:
        slug = pathogen.lower().replace(" ", "_")
        path = settings.data_processed_dir / f"curated_{slug}.csv"
        df = pd.read_csv(path)
        df = df[df["pX"].notna()].copy()  # exactas + censuradas-con-cota
        df["pathogen"] = pathogen
        df["censor_dir"] = df["relation"].map(_RELATION_TO_DIR)
        # filas exactas traen relation "=" (no nulo); las de coadd exactas
        # tambien. Cualquier relation no mapeada -> tratar como exacta.
        df["censor_dir"] = df["censor_dir"].fillna(_DIR_EQ).astype(int)
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    _assert_censor_direction(all_df)  # sobre datos completos, antes de subsetear
    if cfg.limit_rows is not None:
        all_df = all_df.sample(
            n=min(cfg.limit_rows, len(all_df)), random_state=cfg.seed
        ).reset_index(drop=True)
    return all_df


def _split_by_inchikey(
    df: pd.DataFrame, cfg: TrainConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split por compuesto (inchikey) para evitar fuga del mismo compuesto a
    train y test, estratificado por is_hit y separado por patogeno."""
    rng = np.random.default_rng(cfg.seed)
    test_keys: set[str] = set()

    for pathogen, sub in df.groupby("pathogen"):
        # a cada inchikey se le asigna is_hit=True si ALGUNA de sus filas lo es,
        # para estratificar a nivel de compuesto.
        by_key = sub.groupby("inchikey")["is_hit"].any()
        for hit_flag in (True, False):
            keys = by_key[by_key == hit_flag].index.to_numpy()
            rng.shuffle(keys)
            n_test = int(round(len(keys) * cfg.test_frac))
            test_keys.update(keys[:n_test].tolist())

    is_test = df["inchikey"].isin(test_keys)
    train_df = df[~is_test].reset_index(drop=True)
    test_df = df[is_test].reset_index(drop=True)

    # assert de suficientes positivos en test por patogeno (metrica de clase
    # minoritaria fiable en Fase 7)
    for pathogen, sub in test_df.groupby("pathogen"):
        n_hits = int(sub["is_hit"].sum())
        if n_hits < cfg.min_test_hits and cfg.limit_rows is None:
            raise ValueError(
                f"test de '{pathogen}' tiene solo {n_hits} hits "
                f"(< min {cfg.min_test_hits}); revisar el split."
            )
    return train_df, test_df


def _persist_test_split(test_df: pd.DataFrame, cfg: TrainConfig) -> Path:
    """Guarda los inchikeys de test a disco para que Fase 7 evalue EXACTAMENTE
    el mismo hold-out sin depender de regenerarlo por semilla."""
    payload = {
        "seed": cfg.seed,
        "test_frac": cfg.test_frac,
        "inchikeys": {
            pathogen: sorted(sub["inchikey"].unique().tolist())
            for pathogen, sub in test_df.groupby("pathogen")
        },
    }
    path = settings.data_processed_dir / "split_test_inchikeys.json"
    path.write_text(json.dumps(payload, indent=2))
    print(f"[fase3] split de test persistido en {path}")
    return path


def _build_samples(
    df: pd.DataFrame, gyra_by_pathogen: dict[str, str], tokenizer_op, device: str
) -> list[dict]:
    """Pre-tokeniza cada fila una sola vez (target=GyrA del patogeno,
    drug=SMILES) y adjunta el label pMIC y la direccion de censura."""
    samples = []
    for row in df.itertuples(index=False):
        sample = _build_encoder_input(
            smiles=row.smiles,
            protein_sequence=gyra_by_pathogen[row.pathogen],
            tokenizer_op=tokenizer_op,
            device=device,
        )
        sample["_y"] = float(row.pX)
        sample["_dir"] = int(row.censor_dir)
        samples.append(sample)
    return samples


# --------------------------------------------------------------------------- #
# Loss
# --------------------------------------------------------------------------- #
def _censored_loss(
    pred_norm: torch.Tensor, y_norm: torch.Tensor, direction: torch.Tensor
) -> torch.Tensor:
    """MSE para exactas + hinge simetrico para censuradas, en el espacio
    normalizado del checkpoint. pred/y ya normalizados."""
    diff = pred_norm - y_norm
    loss_eq = diff**2
    loss_upper = torch.relu(diff) ** 2  # penaliza pred > cota superior
    loss_lower = torch.relu(-diff) ** 2  # penaliza pred < cota inferior
    loss = torch.where(
        direction == _DIR_UPPER,
        loss_upper,
        torch.where(direction == _DIR_LOWER, loss_lower, loss_eq),
    )
    return loss.mean()


def _forward_loss(base_model, batch: list[dict], device: str) -> torch.Tensor:
    y = torch.tensor([s["_y"] for s in batch], dtype=torch.float32, device=device)
    direction = torch.tensor([s["_dir"] for s in batch], device=device)
    y_norm = (y - NORM_Y_MEAN) / NORM_Y_STD

    batch_dict = base_model.forward_encoder_only(batch)
    pred_norm = batch_dict[SCALARS_PREDICTION_HEAD_LOGITS][:, 0]
    return _censored_loss(pred_norm, y_norm, direction)


@torch.no_grad()
def _evaluate(base_model, samples: list[dict], cfg: TrainConfig) -> dict:
    """Loss censurada (todas las filas) + RMSE en unidades pMIC reales sobre
    el subconjunto EXACTO. Restaura el modo train al salir."""
    was_training = base_model.training
    base_model.eval()
    total_loss, sq_err = 0.0, []
    for start in range(0, len(samples), cfg.batch_size):
        batch = samples[start : start + cfg.batch_size]
        y = torch.tensor([s["_y"] for s in batch], dtype=torch.float32, device=cfg.device)
        direction = torch.tensor([s["_dir"] for s in batch], device=cfg.device)
        y_norm = (y - NORM_Y_MEAN) / NORM_Y_STD
        batch_dict = base_model.forward_encoder_only(batch)
        pred_norm = batch_dict[SCALARS_PREDICTION_HEAD_LOGITS][:, 0]
        total_loss += _censored_loss(pred_norm, y_norm, direction).item() * len(batch)
        pred_denorm = pred_norm * NORM_Y_STD + NORM_Y_MEAN
        eq_mask = direction == _DIR_EQ
        if eq_mask.any():
            sq_err.extend(((pred_denorm[eq_mask] - y[eq_mask]) ** 2).tolist())
    if was_training:
        base_model.train()
    return {
        "loss": total_loss / max(1, len(samples)),
        "rmse_exact": float(np.sqrt(np.mean(sq_err))) if sq_err else float("nan"),
        "n": len(samples),
        "n_exact": len(sq_err),
    }


# --------------------------------------------------------------------------- #
# Entrenamiento
# --------------------------------------------------------------------------- #
class _UnusedHeadStub(torch.nn.Module):
    """Sustituto barato de `encoder_head`: su salida (LOGITS/SCORES/CLS_PRED de
    token-classification) no interviene en la loss de regresion, pero
    `forward_encoder_only` la calcula igualmente. Proyectar hidden->vocab sobre
    1512 posiciones (106M params) es coste y memoria de activaciones inutiles;
    devolver las 2 primeras dims del hidden basta para que softmax/argmax
    posteriores no fallen."""

    def forward(self, x):
        return x[..., :2]


def _prepare_for_training(model) -> None:
    """Recorta memoria/computo para que el entrenamiento a seq 1512 quepa en la
    GPU de 8GB: (1) neutraliza la cabeza de vocabulario no usada, (2) activa
    gradient checkpointing en el encoder T5 (recomputa activaciones en el
    backward en vez de guardarlas — sin esto, batch 1 hace OOM)."""
    model.encoder_head = _UnusedHeadStub()
    model.t5_model.encoder.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )


def _apply_lora(model, cfg: TrainConfig):
    lora_cfg = LoraConfig(
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        target_modules=_LORA_TARGET_REGEX,
        modules_to_save=_LORA_MODULES_TO_SAVE,
        bias="none",
    )
    peft_model = get_peft_model(model, lora_cfg)
    return peft_model


def _iter_batches(samples: list[dict], batch_size: int, rng: np.random.Generator):
    idx = np.arange(len(samples))
    rng.shuffle(idx)
    for start in range(0, len(idx), batch_size):
        yield [samples[i] for i in idx[start : start + batch_size]]


def run_lora_finetune(cfg: TrainConfig | None = None, pilot: bool = False) -> dict:
    cfg = cfg or TrainConfig()
    torch.manual_seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)

    print("[fase3] cargando checkpoint base (modelo + tokenizer)...")
    model, tokenizer_op = load_model(cfg.device)
    _prepare_for_training(model)  # neutraliza head no usada + gradient checkpointing

    gyra_by_pathogen = {p: _fetch_gyra_sequence(p) for p in settings.pathogens}
    for p, seq in gyra_by_pathogen.items():
        print(f"[fase3] ancla GyrA {p}: {len(seq)} aa ({GYRA_ANCHORS[p]['accession']})")

    df = _load_trainable_rows(cfg)
    train_df, test_df = _split_by_inchikey(df, cfg)
    print(
        f"[fase3] filas entrenables: {len(df)} | train={len(train_df)} "
        f"test={len(test_df)} | hits train={int(train_df['is_hit'].sum())} "
        f"test={int(test_df['is_hit'].sum())}"
    )

    # --- Piloto de timing: tokeniza una muestra REPRESENTATIVA y corre unos
    # pocos pasos para proyectar el coste de una epoca. No toca test/baseline.
    if pilot:
        peft_model = _apply_lora(model, cfg)
        base_model = peft_model.base_model.model
        peft_model.print_trainable_parameters()
        optimizer = torch.optim.AdamW(
            (p for p in peft_model.parameters() if p.requires_grad), lr=cfg.lr
        )

        n_pilot_rows = min(len(train_df), cfg.pilot_steps * cfg.batch_size)
        print(f"[pilot] pre-tokenizando {n_pilot_rows} filas (muestra aleatoria)...")
        t_tok0 = time.perf_counter()
        pilot_df = train_df.sample(n=n_pilot_rows, random_state=cfg.seed)
        pilot_samples = _build_samples(
            pilot_df, gyra_by_pathogen, tokenizer_op, cfg.device
        )
        tok_per_row = (time.perf_counter() - t_tok0) / max(1, n_pilot_rows)

        peft_model.train()
        if cfg.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        step_times = []
        for step, batch in enumerate(_iter_batches(pilot_samples, cfg.batch_size, rng)):
            if step >= cfg.pilot_steps:
                break
            t0 = time.perf_counter()
            optimizer.zero_grad()
            loss = _forward_loss(base_model, batch, cfg.device)
            loss.backward()
            optimizer.step()
            if cfg.device == "cuda":
                torch.cuda.synchronize()  # tiempo real, no solo el encolado async
            step_times.append(time.perf_counter() - t0)

        peak_vram_gb = (
            torch.cuda.max_memory_allocated() / 1e9 if cfg.device == "cuda" else 0.0
        )
        s_per_step = float(np.median(step_times))
        steps_per_epoch = int(np.ceil(len(train_df) / cfg.batch_size))
        epoch_h = s_per_step * steps_per_epoch / 3600
        print(
            f"\n[pilot] device={cfg.device} batch={cfg.batch_size}"
            + (f" | pico VRAM {peak_vram_gb:.2f} GB" if cfg.device == "cuda" else "")
        )
        print(
            f"[pilot] tokenizacion: {tok_per_row*1000:.1f} ms/fila "
            f"({tok_per_row*len(train_df)/60:.1f} min para {len(train_df)} filas)"
        )
        print(
            f"[pilot] paso (fwd+bwd): mediana {s_per_step:.2f}s "
            f"(min {min(step_times):.2f}, max {max(step_times):.2f})"
        )
        print(
            f"[pilot] proyeccion: {steps_per_epoch} pasos/epoca -> ~{epoch_h:.1f} h/epoca"
        )
        return {
            "device": cfg.device,
            "s_per_step": s_per_step,
            "peak_vram_gb": peak_vram_gb,
            "tok_per_row_ms": tok_per_row * 1000,
            "steps_per_epoch": steps_per_epoch,
            "projected_epoch_hours": epoch_h,
        }

    # --- Run real ---------------------------------------------------------- #
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _persist_test_split(test_df, cfg)

    # Subconjunto fijo de test para eval periodica (coste CPU); Fase 7 usara el
    # test completo persistido. Mismo subset para baseline, eval y final.
    eval_df = test_df.sample(n=min(cfg.eval_subset, len(test_df)), random_state=cfg.seed)
    print(f"[fase3] pre-tokenizando {len(eval_df)} filas de eval (subset de test)...")
    eval_samples = _build_samples(eval_df, gyra_by_pathogen, tokenizer_op, cfg.device)

    # BASELINE obligatorio: modelo base (sin LoRA) sobre el test, antes de nada.
    baseline = _evaluate(model, eval_samples, cfg)
    print(
        f"[baseline] modelo base SIN LoRA: loss={baseline['loss']:.4f} "
        f"rmse_exact={baseline['rmse_exact']:.3f} pMIC "
        f"(n={baseline['n']}, exactas={baseline['n_exact']})"
    )

    peft_model = _apply_lora(model, cfg)
    base_model = peft_model.base_model.model  # Mammal con LoRA inyectado
    peft_model.print_trainable_parameters()
    optimizer = torch.optim.AdamW(
        (p for p in peft_model.parameters() if p.requires_grad), lr=cfg.lr
    )

    print(f"[fase3] pre-tokenizando {len(train_df)} filas de train...")
    train_samples = _build_samples(train_df, gyra_by_pathogen, tokenizer_op, cfg.device)

    peft_model.train()
    history = []
    global_step = 0  # pasos de OPTIMIZADOR (tras acumular grad_accum micro-batches)
    micro = 0
    running_loss = 0.0
    stop = False
    optimizer.zero_grad()
    for epoch in range(cfg.epochs):
        for batch in _iter_batches(train_samples, cfg.batch_size, rng):
            loss = _forward_loss(base_model, batch, cfg.device) / cfg.grad_accum
            loss.backward()
            running_loss += loss.item()
            micro += 1
            if micro % cfg.grad_accum != 0:
                continue

            optimizer.step()
            optimizer.zero_grad()
            global_step += 1

            if global_step % 20 == 0:
                print(f"[fase3] step {global_step} train_loss {running_loss:.4f}")
            running_loss = 0.0
            if global_step % cfg.eval_every == 0:
                ev = _evaluate(base_model, eval_samples, cfg)
                ev["step"] = global_step
                history.append(ev)
                print(
                    f"[eval] step {global_step} loss {ev['loss']:.4f} "
                    f"rmse_exact {ev['rmse_exact']:.3f} pMIC"
                )
            if global_step % cfg.ckpt_every == 0:
                ckpt = _OUTPUT_DIR / f"lora_adapter_step{global_step}"
                peft_model.save_pretrained(str(ckpt))
                print(f"[ckpt] guardado {ckpt}")
            if cfg.max_steps is not None and global_step >= cfg.max_steps:
                stop = True
                break
        if stop:
            break

    final = _evaluate(base_model, eval_samples, cfg)
    out = _OUTPUT_DIR / "lora_adapter"
    peft_model.save_pretrained(str(out))

    metrics = {
        "baseline": baseline,
        "final": final,
        "history": history,
        "steps": global_step,
        "config": {
            "lora_r": cfg.lora_r,
            "lora_alpha": cfg.lora_alpha,
            "lr": cfg.lr,
            "batch_size": cfg.batch_size,
            "grad_accum": cfg.grad_accum,
            "device": cfg.device,
            "eval_subset": cfg.eval_subset,
            "seed": cfg.seed,
        },
    }
    (_OUTPUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(
        f"[fase3] adapter -> {out} (steps={global_step}) | "
        f"rmse_exact baseline {baseline['rmse_exact']:.3f} -> "
        f"final {final['rmse_exact']:.3f} pMIC"
    )
    return {"steps": global_step, "adapter_dir": str(out), "baseline": baseline, "final": final}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune LoRA DTI (Fase 3)")
    parser.add_argument("--pilot", action="store_true", help="medir timing y salir")
    parser.add_argument("--pilot-steps", type=int, default=60)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--limit-rows", type=int, default=None)
    parser.add_argument("--eval-every", type=int, default=200)
    parser.add_argument("--ckpt-every", type=int, default=500)
    args = parser.parse_args()

    cfg = TrainConfig(
        max_steps=args.max_steps,
        epochs=args.epochs,
        batch_size=args.batch_size,
        grad_accum=args.grad_accum,
        lr=args.lr,
        limit_rows=args.limit_rows,
        pilot_steps=args.pilot_steps,
        eval_every=args.eval_every,
        ckpt_every=args.ckpt_every,
    )
    run_lora_finetune(cfg, pilot=args.pilot)


if __name__ == "__main__":
    main()
