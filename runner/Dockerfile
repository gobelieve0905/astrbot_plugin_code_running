FROM m.daocloud.io/docker.io/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
RUN pip install --index-url https://mirrors.aliyun.com/pypi/simple --no-cache-dir --only-binary=:all: numpy==2.2.6 pandas==2.2.3 matplotlib==3.10.3 openpyxl==3.1.5
USER 65532:65532
WORKDIR /work
