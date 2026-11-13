FROM nginx:stable-alpine

COPY frontend/index.html /usr/share/nginx/html/index.html

COPY frontend/src/ /usr/share/nginx/html/src/
COPY frontend/styles/ /usr/share/nginx/html/styles/
COPY frontend/public/ /usr/share/nginx/html/public/

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
