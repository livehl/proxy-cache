FROM python:3.10-slim
ENV PYTHONIOENCODING=utf-8
WORKDIR /opt/
EXPOSE 80
RUN   pip3 install -i https://mirrors.volces.com/pypi/simple/ uvicorn[standard] fastapi itsdangerous  python-multipart
COPY [".", "/opt/"]
RUN  pip3 install  -r requirements.txt -i https://mirrors.volces.com/pypi/simple/
CMD  uvicorn main:app --host 0.0.0.0  --proxy-headers --port 80