# syntax=docker/dockerfile:1

# whisper.cpp, the same version as on the Mac where the regression transcripts were made.
# Built where the image is built (the server), so it uses that CPU's instructions.
FROM python:3.15.0-slim-trixie AS whisper
ARG WHISPER_CPP_VERSION=v1.9.5
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${WHISPER_CPP_VERSION}" https://github.com/ggml-org/whisper.cpp /src \
 && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF \
 && cmake --build /build -j"$(nproc)" --target whisper-cli

FROM python:3.15.0-slim-trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=whisper /build/bin/whisper-cli /usr/local/bin/whisper-cli

WORKDIR /app
COPY pyproject.toml README.md ./
COPY huddle_notes ./huddle_notes
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 --no-create-home huddle

USER huddle
ENV HUDDLE_DATA_DIR=/data PYTHONUNBUFFERED=1
ENTRYPOINT ["huddle-notes"]
CMD ["--help"]
