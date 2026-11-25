# Runs the SDK Release Kit MCP server on stdio.
# Inside a container it can plan builds and report status; real Android/iOS builds
# need the host toolchains (Xcode, Gradle, Flutter), so run it natively for those.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY recipes ./recipes
COPY web ./web
COPY examples ./examples

RUN pip install --no-cache-dir . \
 && useradd --create-home kit
USER kit
ENV SDK_RELEASE_KIT_HOME=/home/kit/.sdk-release-kit \
    PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

ENTRYPOINT ["sdk-release-kit", "mcp"]
