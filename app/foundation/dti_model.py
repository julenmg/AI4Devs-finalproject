"""Fase 2 - Carga y valida el checkpoint DTI de IBM.

Uso previsto:
    from biomed_multi_alignment import Mammal
    model = Mammal.from_pretrained(settings.dti_checkpoint)

TODO: envolver la carga y anadir predict(smiles, protein_seq) -> binding_affinity.
"""


def load_model():
    raise NotImplementedError


def predict_affinity(smiles: str, protein_sequence: str) -> float:
    raise NotImplementedError
