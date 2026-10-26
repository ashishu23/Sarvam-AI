from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8-sig', extra='ignore')
    app_env: Literal['development', 'test', 'production'] = 'development'
    storage_mode: Literal['local', 'supabase'] = 'local'
    local_data_dir: Path = ROOT / 'private-data'
    sarvam_api_key: SecretStr = SecretStr('')
    sarvam_chat_model: str = 'sarvam-105b-conversations'
    sarvam_analysis_model: str = 'sarvam-105b'
    sarvam_stt_model: str = 'saaras:v4'
    sarvam_tts_model: str = 'bulbul:v4-flash'
    sarvam_translate_model: str = 'mayura:v1'
    sarvam_tts_voice_hi: str = 'shubh_enhi_ads'
    sarvam_tts_voice_en: str = 'shubh_enhi_ads'
    supabase_url: str = ''
    supabase_publishable_key: str = ''
    supabase_storage_bucket: str = 'case-documents'
    cors_origins: str = 'http://localhost:5173,http://127.0.0.1:5173'
    max_upload_mb: int = Field(3, ge=1, le=3)
    max_upload_batch_mb: int = Field(3, ge=1, le=3)
    max_request_bytes: int = Field(4_000_000, ge=1_000_000, le=4_000_000)
    max_document_pages: int = Field(10, ge=1, le=10)
    max_audio_seconds: int = Field(25, ge=1, le=25)
    max_requests_per_user_per_hour: int = Field(60, ge=1)
    max_document_pages_per_user_per_day: int = Field(30, ge=1)
    max_audio_seconds_per_user_per_day: int = Field(300, ge=1)
    max_model_concurrency: int = Field(2, ge=1, le=8)
    model_request_timeout_seconds: int = 60
    document_job_timeout_seconds: int = 600
    ai_enabled: bool = True

    @property
    def origins(self) -> list[str]:
        return [s.strip() for s in self.cors_origins.split(',') if s.strip()]

    @model_validator(mode='after')
    def production_safety(self):
        if self.app_env == 'production' and self.storage_mode != 'supabase':
            raise ValueError('Production requires STORAGE_MODE=supabase; local sessions are development only.')
        if self.storage_mode == 'supabase' and (not self.supabase_url.startswith('https://') or not self.supabase_publishable_key):
            raise ValueError('Configure an HTTPS SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.')
        if self.supabase_publishable_key.startswith('sb_secret_'):
            raise ValueError('Use the Supabase publishable/anon key, never an elevated secret key.')
        if self.supabase_publishable_key.count('.') == 2:
            import base64
            import json
            try:
                claim = self.supabase_publishable_key.split('.')[1]
                role = json.loads(base64.urlsafe_b64decode(claim + '=' * (-len(claim) % 4))).get('role')
            except (ValueError, KeyError):
                raise ValueError('Invalid Supabase public key format.') from None
            if role != 'anon':
                raise ValueError('Use the Supabase publishable/anon key, never a service-role key.')
        if '*' in self.origins:
            raise ValueError('CORS_ORIGINS must name exact application origins.')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
