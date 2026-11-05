# syntax=docker/dockerfile:1

# Bitcoin Knots with BLAKE2b, from the official release binaries. The version and the sha256 of each
# architecture's tarball are build arguments, set in .env.

FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS base

FROM base AS download

ARG KNOTS_VERSION
ARG KNOTS_SHA256_AMD64
ARG KNOTS_SHA256_ARM64
ARG TARGETARCH

# Package versions are not pinned: Debian removes old versions from its mirrors, and curl only downloads a
# tarball whose sha256 is checked below.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/knots

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN : "${KNOTS_VERSION:?KNOTS_VERSION is required}"; \
    case "${TARGETARCH}" in \
      amd64) arch=x86_64; sha256="${KNOTS_SHA256_AMD64:?KNOTS_SHA256_AMD64 is required}" ;; \
      arm64) arch=aarch64; sha256="${KNOTS_SHA256_ARM64:?KNOTS_SHA256_ARM64 is required}" ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    tarball="bitcoin-${KNOTS_VERSION}-${arch}-linux-gnu.tar.gz"; \
    curl -fsSLO "https://bitcoinknots.org/files/${KNOTS_VERSION%%.*}.x/${KNOTS_VERSION}/${tarball}"; \
    echo "${sha256}  ${tarball}" | sha256sum -c -; \
    tar -xzf "${tarball}" --strip-components=1

FROM base

ARG KNOTS_VERSION

LABEL org.opencontainers.image.title="Bitcoin Knots with BLAKE2b" \
      org.opencontainers.image.source="https://github.com/ashbornelabs/xbt-infra" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${KNOTS_VERSION}"

COPY --from=download /tmp/knots/bin/bitcoind /tmp/knots/bin/bitcoin-cli /usr/local/bin/

RUN groupadd --system --gid 10001 bitcoin \
    && useradd --system --uid 10001 --gid bitcoin --home-dir /home/bitcoin --create-home bitcoin \
    && mkdir /data \
    && chown bitcoin:bitcoin /data

USER 10001:10001
WORKDIR /home/bitcoin

ENTRYPOINT ["bitcoind"]
CMD ["-datadir=/data"]
