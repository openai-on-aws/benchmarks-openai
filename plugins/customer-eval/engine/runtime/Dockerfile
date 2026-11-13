FROM python:3.12-slim
WORKDIR /app
COPY requirements-integration.txt ./
RUN pip install --no-cache-dir -r requirements-integration.txt
COPY customer_eval ./customer_eval
COPY agent ./agent
COPY customer_workloads ./customer_workloads
COPY intake ./intake
COPY hillclimb.py optimizer.py ./
COPY runtime ./runtime
ENV PYTHONPATH=/app EVAL_USE_EXECUTION_ROLE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 evaluation && chown -R evaluation:evaluation /app
USER evaluation
EXPOSE 8080
CMD ["python", "runtime/app.py"]
