# The template is part of the image (envsubst'd at container start); `docker
# compose watch` syncs edits and restarts nginx.
FROM nginx:1.29-alpine
COPY templates/ /etc/nginx/templates/
