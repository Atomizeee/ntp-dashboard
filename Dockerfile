FROM python:3.13-alpine

# chronyc is the only extra tool needed (chronyd itself is never started here)
RUN apk add --no-cache chrony tzdata \
 && adduser -D -u 10001 app \
 && mkdir /data && chown app /data

WORKDIR /srv
COPY app ./app

ENV PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
USER app
EXPOSE 8080
VOLUME /data

HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=3)"

CMD ["python", "-m", "app"]
