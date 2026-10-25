# One image for a standalone container (e.g. next to Whisper) and for the Home Assistant add-on.
# Debian (glibc), because sherpa-onnx has no musl wheels (HA base images are Alpine).
ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SPEAKER_ID_MODELS=/models

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# model baked into the image - no download on first start
ARG MODEL=3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx
ADD https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/${MODEL} /models/${MODEL}

COPY wyoming_speaker_id /app/wyoming_speaker_id
WORKDIR /app

EXPOSE 10350 10351
ENTRYPOINT ["python", "-m", "wyoming_speaker_id"]
