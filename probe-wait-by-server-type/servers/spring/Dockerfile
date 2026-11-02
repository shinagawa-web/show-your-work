ARG MAVEN_IMAGE
ARG JRE_IMAGE
FROM ${MAVEN_IMAGE} AS build
WORKDIR /src
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src src
RUN mvn -q -B package -DskipTests

FROM ${JRE_IMAGE}
COPY --from=build /src/target/app.jar /app/app.jar
