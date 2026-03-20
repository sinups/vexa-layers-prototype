FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY proxy.py /srv/proxy.py

CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8000"]
