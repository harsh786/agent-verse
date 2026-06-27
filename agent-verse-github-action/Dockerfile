FROM python:3.12-slim
RUN pip install httpx --quiet
COPY entrypoint.py /entrypoint.py
ENTRYPOINT ["python3", "/entrypoint.py"]
