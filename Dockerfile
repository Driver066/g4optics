# Native ARM64 GUI runtime; the base is pinned independently of mutable tags.
FROM carlomt/geant4@sha256:6ca84898d1346dfd9b511251dc158eada0f906fdcae6d9139415c0c3f932e7b6

# Scan macro generation needs Python; ROOT/Python analysis stays on the host.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work/g4optics/test/OpNovice2
