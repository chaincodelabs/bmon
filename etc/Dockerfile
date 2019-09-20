FROM python:3.7

WORKDIR /src
RUN apt-get -qq update && \
  apt-get install -qq -y libpq-dev && \
  pip install pipenv && \
  pip install psycopg2

COPY ./Pipfile /src/Pipfile
COPY ./etc/docker_entrypoint.sh /entrypoint.sh
RUN pipenv install --dev && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
