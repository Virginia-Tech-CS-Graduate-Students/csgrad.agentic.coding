FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

RUN python -m pip install --no-cache-dir pytest==9.1.1 \
    && mkdir -p /product /tests /out /deps \
    && chmod 755 /product /tests /out /deps
COPY runner_entrypoint.py /opt/workbench_runner.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
ENTRYPOINT ["python", "-I", "/opt/workbench_runner.py"]
