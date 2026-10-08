FROM node:22.23.3-bookworm-slim AS build
WORKDIR /app
COPY app/package.json app/package-lock.json ./
RUN npm ci
COPY app/ ./
ARG RELOAD_ON_PRELOAD_ERROR=0
RUN VITE_APP_VERSION=v1 VITE_RELOAD_ON_PRELOAD_ERROR=$RELOAD_ON_PRELOAD_ERROR npx vite build --outDir /out/v1 \
 && VITE_APP_VERSION=v2 VITE_RELOAD_ON_PRELOAD_ERROR=$RELOAD_ON_PRELOAD_ERROR npx vite build --outDir /out/v2
ARG VERSION=v1
ARG KEEP_OLD_ASSETS=0
RUN mkdir /site && cp -r /out/$VERSION/. /site/ \
 && if [ "$KEEP_OLD_ASSETS" = 1 ]; then cp -rn /out/v1/assets/. /site/assets/; fi \
 && find /site -type f | sort > /site-files.txt

FROM nginx:1.30.5
COPY --from=build /site/ /usr/share/nginx/html/
COPY --from=build /site-files.txt /site-files.txt
COPY server/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
