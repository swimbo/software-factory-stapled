FROM python@sha256:f9fa7f851e38bfb19c9de3afbc4b86ae7176ea7aaf94535c31df5458d5849457
WORKDIR /app
COPY . /app/
LABEL factory.git-revision=d8bf750a3c8c0b03c3481b9d8c07b508fc3fc3f3 factory.source-archive-sha256=6f2d08e56466a95b2f14b1fbaefccd32b0740fc9f08484a9ec576084f5700457
USER 65534:65534
ENTRYPOINT ["python3","-c","from http.server import HTTPServer;from app import Handler;HTTPServer(('127.0.0.1',8000),Handler).serve_forever()"]
