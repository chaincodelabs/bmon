FROM docker.io/library/python:3.11.0rc2-bullseye

WORKDIR /src
RUN apt-get -qq update && apt-get install -qq -y libpq-dev netcat iproute2

COPY . ./
COPY ./etc/docker_entrypoint.sh /entrypoint.sh
RUN pip install --upgrade pip setuptools && \
  pip install -e . && \
  chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
