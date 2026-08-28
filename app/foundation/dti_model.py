"""Fase 2 - Carga y valida el checkpoint DTI de IBM (MAMMAL).

El checkpoint `ibm-research/biomed.omics.bl.sm.ma-ted-458m.dti_bindingdb_pkd`
predice afinidad de union farmaco-diana como un escalar pKd
(-log10(Kd en molar)); a mayor pKd, mayor afinidad. La entrada es un par
(secuencia de aminoacidos de la proteina diana, SMILES del farmaco).

FRONTERA DEL MODELO (mantener clara, ver CLAUDE.md): esto predice afinidad
de union a nivel molecular, NO eficacia clinica ni resistencia real en el
organismo. El fine-tune de la Fase 3 es un QSAR de potencia fenotipica
(SMILES->pMIC) montado sobre esta base, no binding especifico.

Sobre el preprocesado inline: el ejemplo oficial del paquete
(`mammal.examples.dti_bindingdb_kd.task.DtiBindingdbKdTask`) trae los dos
metodos que necesitamos para inferencia (`data_preprocessing` y
`process_model_output`), pero ese modulo importa a nivel de fichero
`tdc` (Therapeutics Data Commons, solo para el DataModule de entrenamiento),
y `tdc` arrastra `rdkit-pypi`, que entra en conflicto con el `rdkit` del
proyecto y rompe el entorno. Para no meter una dependencia de entrenamiento
(y toxica) en la ruta de inferencia, se replican aqui esos dos metodos,
copiados fielmente de biomed-multi-alignment==0.2.5. Si se actualiza el
paquete, revisar que este formato de tokens sigue coincidiendo con el que
espera el checkpoint (riesgo de drift asumido conscientemente).
"""
from __future__ import annotations

from functools import lru_cache

import torch
from fuse.data.tokenizers.modular_tokenizer.op import ModularTokenizerOp

from mammal.keys import (
    ENCODER_INPUTS_ATTENTION_MASK,
    ENCODER_INPUTS_SCALARS,
    ENCODER_INPUTS_STR,
    ENCODER_INPUTS_TOKENS,
    SCALARS_PREDICTION_HEAD_LOGITS,
)
from mammal.model import Mammal

from app.config import settings

# Media/std usadas al fine-tunear el checkpoint sobre BindingDB Kd. Son
# necesarias para de-normalizar la salida del modelo a un pKd interpretable
# (valores tomados del ejemplo oficial del paquete).
NORM_Y_MEAN = 5.79384684128215
NORM_Y_STD = 1.33808027428196

_OUTPUT_KEY = "model.out.dti_bindingdb_kd"
# Autodeteccion de device: usa GPU si torch la ve utilizable, si no CPU.
# Nota de compatibilidad: la GPU local (GTX 1070, Pascal) solo funciona con una
# build de torch para CUDA 12.x; el torch instalado (cu130) reporta
# is_available()==False sobre el driver 12.2, asi que hoy esto resuelve a "cpu"
# hasta que se alinee torch con el driver (ver docs/decisions.md, Fase 3).
_DEFAULT_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Longitudes maximas del ejemplo oficial de DTI (task.data_preprocessing).
_TARGET_MAX_SEQ_LENGTH = 1250
_DRUG_MAX_SEQ_LENGTH = 256
_ENCODER_INPUT_MAX_SEQ_LEN = 1512


@lru_cache(maxsize=1)
def load_model(device: str = _DEFAULT_DEVICE) -> tuple[Mammal, ModularTokenizerOp]:
    """Descarga (la primera vez) y carga el modelo + tokenizer desde el
    checkpoint configurado. Cacheado: la carga es cara y solo debe pasar una
    vez por proceso."""
    checkpoint = settings.dti_checkpoint
    tokenizer_op = ModularTokenizerOp.from_pretrained(checkpoint)
    model = Mammal.from_pretrained(checkpoint)
    model.eval()
    model.to(device=device)
    return model, tokenizer_op


def _build_encoder_input(
    smiles: str,
    protein_sequence: str,
    tokenizer_op: ModularTokenizerOp,
    device: str | torch.device,
) -> dict:
    """Replica de DtiBindingdbKdTask.data_preprocessing (rama de inferencia,
    sin ground truth). Copiado de biomed-multi-alignment==0.2.5."""
    sample_dict: dict = {}
    sample_dict[ENCODER_INPUTS_STR] = (
        "<@TOKENIZER-TYPE=AA><MASK>"
        f"<@TOKENIZER-TYPE=AA@MAX-LEN={_TARGET_MAX_SEQ_LENGTH}><MOLECULAR_ENTITY><MOLECULAR_ENTITY_GENERAL_PROTEIN><SEQUENCE_NATURAL_START>{protein_sequence}<SEQUENCE_NATURAL_END>"
        f"<@TOKENIZER-TYPE=SMILES@MAX-LEN={_DRUG_MAX_SEQ_LENGTH}><MOLECULAR_ENTITY><MOLECULAR_ENTITY_SMALL_MOLECULE><SEQUENCE_NATURAL_START>{smiles}<SEQUENCE_NATURAL_END>"
        "<EOS>"
    )
    tokenizer_op(
        sample_dict,
        key_in=ENCODER_INPUTS_STR,
        key_out_tokens_ids=ENCODER_INPUTS_TOKENS,
        key_out_attention_mask=ENCODER_INPUTS_ATTENTION_MASK,
        max_seq_len=_ENCODER_INPUT_MAX_SEQ_LEN,
        key_out_scalars=ENCODER_INPUTS_SCALARS,
    )
    sample_dict[ENCODER_INPUTS_TOKENS] = torch.tensor(
        sample_dict[ENCODER_INPUTS_TOKENS], device=device
    )
    sample_dict[ENCODER_INPUTS_ATTENTION_MASK] = torch.tensor(
        sample_dict[ENCODER_INPUTS_ATTENTION_MASK], device=device
    )
    return sample_dict


def predict_affinity(smiles: str, protein_sequence: str) -> float:
    """Predice la afinidad de union (pKd) para un par farmaco-diana.

    :param smiles: representacion SMILES del farmaco.
    :param protein_sequence: secuencia de aminoacidos de la proteina diana.
    :return: pKd predicho (float). A mayor valor, mayor afinidad de union.
    """
    model, tokenizer_op = load_model()

    sample_dict = _build_encoder_input(
        smiles=smiles,
        protein_sequence=protein_sequence,
        tokenizer_op=tokenizer_op,
        device=model.device,
    )

    with torch.no_grad():
        batch_dict = model.forward_encoder_only([sample_dict])

    # Replica de DtiBindingdbKdTask.process_model_output: de-normaliza el
    # escalar predicho a pKd. Copiado de biomed-multi-alignment==0.2.5.
    scalars_preds = batch_dict[SCALARS_PREDICTION_HEAD_LOGITS]
    pkd = scalars_preds[:, 0] * NORM_Y_STD + NORM_Y_MEAN
    return float(pkd[0])
