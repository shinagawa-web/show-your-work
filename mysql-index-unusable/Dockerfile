FROM composer:2.10.3 AS vendor
WORKDIR /app
COPY composer.json ./
RUN composer install --no-dev --no-interaction --no-progress --ignore-platform-reqs

FROM php:8.4.26-cli
RUN docker-php-ext-install pdo_mysql
WORKDIR /app
COPY --from=vendor /app/vendor vendor
COPY laravel.php ./
