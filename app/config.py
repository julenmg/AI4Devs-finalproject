"""Configuracion central: rutas, checkpoint, patogenos elegidos, claves de API.
Fase 1-2.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    dti_checkpoint: str = "ibm-research/biomed.omics.bl.sm.ma-ted-458m.dti_bindingdb_pkd"
    pathogens: list[str] = ["Klebsiella pneumoniae"]  # ajusta a tu alcance (1-2 patogenos)
    data_raw_dir: Path = Path("data/raw")
    data_processed_dir: Path = Path("data/processed")
    anthropic_api_key: str = ""


settings = Settings()
