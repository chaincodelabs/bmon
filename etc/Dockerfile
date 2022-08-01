FROM docker.io/library/python:3

WORKDIR /src
RUN apt-get -qq update && apt-get install -qq -y libpq-dev

COPY ./requirements.txt ./
COPY ./etc/docker_entrypoint.sh /entrypoint.sh
RUN pip install -r requirements.txt && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
