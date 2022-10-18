FROM docker.io/library/node:14

ARG UID=1000
ARG GID=1000

WORKDIR /src
# The .yarnrc file places our node_modules in /node_modules.
COPY docker_entrypoint.sh /entrypoint.sh
RUN chmod +rx /entrypoint.sh
ENV PATH /node_modules/.bin/:$PATH
ENTRYPOINT ["/entrypoint.sh"]
