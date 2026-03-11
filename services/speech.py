import logging
from pathlib import Path

logger = logging.getLogger(__name__)
_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper недоступен для текущего Python. "
                "Для голоса используйте Python 3.12 и установите зависимости."
            ) from exc
        # small — хороший баланс скорости и качества для локальной разработки.
        _MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _MODEL


def transcribe_audio(audio_path: str) -> str:
    """Распознает аудио локально и возвращает текст."""
    path = Path(audio_path)
    if not path.exists():
        logger.error("transcribe_audio | файл не найден: %s", audio_path)
        raise FileNotFoundError(f"Файл не найден: {audio_path}")

    logger.info("transcribe_audio | start path=%s", audio_path)
    model = _get_model()
    segments, _ = model.transcribe(str(path), language="ru")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    logger.info("transcribe_audio | result=%r", text[:80] if text else "(пусто)")
    return text
