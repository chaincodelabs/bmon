FROM docker.io/library/python:3.11.0rc2-bullseye

WORKDIR /src
RUN apt-get -qq update && apt-get install -qq -y libpq-dev netcat iproute2 lshw

# Can pass PYTHON_PKG=.[tests] for test dependencies.
ARG PYTHON_PKG=.

COPY . ./
COPY ./docker/wait-for /bin/wait-for
COPY ./docker/entrypoint.sh /entrypoint.sh
RUN pip install --upgrade pip setuptools ipython && \
  pip install -e ./infra && \
  pip install -e $PYTHON_PKG && \
  chmod +x /entrypoint.sh /bin/wait-for

ENTRYPOINT ["/entrypoint.sh"]
