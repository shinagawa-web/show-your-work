FROM nginx:1.31.6
RUN rm /etc/nginx/conf.d/default.conf \
 && sed -i '/^worker_processes/a worker_shutdown_timeout 1s;' /etc/nginx/nginx.conf
COPY server/site/ /usr/share/nginx/html/
COPY server/links/ /usr/share/nginx/links/
COPY server/docs.conf.template /etc/nginx/docs.conf.template
COPY server/set-target.sh /usr/local/bin/set-target
COPY server/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
