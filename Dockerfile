FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY openbb_snaptrade ./openbb_snaptrade

EXPOSE 8069

CMD ["python", "-m", "openbb_snaptrade.app"]
