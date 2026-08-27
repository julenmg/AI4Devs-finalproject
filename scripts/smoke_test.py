"""Prueba rapida end-to-end: carga el modelo, corre una prediccion de
ejemplo y confirma que el pipeline no esta roto. Pensado para correr en
segundos (una vez descargado el checkpoint), no para evaluar calidad
(eso es evals/).

Uso:
    uv run python -m scripts.smoke_test
"""
import time

from app.foundation.dti_model import predict_affinity

# Par de ejemplo del ejemplo oficial de MAMMAL (una diana y un farmaco
# arbitrarios): sirve para confirmar que carga+forward+de-normalizacion
# producen un pKd finito, no para juzgar si el valor es "correcto".
EXAMPLE_TARGET_SEQ = "NLMKRCTRGFRKLGKCTTLEEEKCKTLYPRGQCTCSDSKMNTHSCDCKSC"
EXAMPLE_DRUG_SMILES = "CC(=O)NCCC1=CNc2c1cc(OC)cc2"


def main() -> None:
    print("[smoke] cargando checkpoint y prediciendo afinidad de ejemplo...")
    t0 = time.perf_counter()
    pkd = predict_affinity(
        smiles=EXAMPLE_DRUG_SMILES, protein_sequence=EXAMPLE_TARGET_SEQ
    )
    elapsed = time.perf_counter() - t0

    assert isinstance(pkd, float), f"esperado float, obtenido {type(pkd)}"
    assert pkd == pkd, "pKd es NaN"  # NaN != NaN
    assert -5.0 < pkd < 20.0, f"pKd fuera de rango plausible: {pkd}"

    print(f"[smoke] OK - pKd predicho = {pkd:.4f} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
