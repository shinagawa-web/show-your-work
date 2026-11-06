SPRING_PROBES = ("/actuator/health/liveness", "/actuator/health/readiness")

SERVERS = {
    "gunicorn": {"name": "gunicorn sync workers", "image": "pws-gunicorn:dev", "build": "gunicorn",
                 "command": ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8080", "--preload", "app:app"],
                 "env": {"PYTHONUNBUFFERED": "1"}, "probes": ("/healthz", "/healthz")},
    "tomcat": {"name": "Spring Boot, Tomcat default", "image": "pws-spring:dev", "build": "spring",
               "command": ["java", "-jar", "/app/app.jar"], "env": {}, "probes": SPRING_PROBES},
    "tomcat-virtual": {"name": "Spring Boot, Tomcat with virtual threads", "image": "pws-spring:dev", "build": "spring",
                       "command": ["java", "-jar", "/app/app.jar"],
                       "env": {"SPRING_THREADS_VIRTUAL_ENABLED": "true"}, "probes": SPRING_PROBES},
    "tomcat-mgmt": {"name": "Spring Boot, Tomcat default, management port separate", "image": "pws-spring:dev", "build": "spring",
                    "command": ["java", "-jar", "/app/app.jar"],
                    "env": {"MANAGEMENT_SERVER_PORT": "8081"}, "probes": SPRING_PROBES, "probe_port": 8081},
    "tomcat-virtual-limit": {"name": "Spring Boot, Tomcat with virtual threads, 1000 connections", "image": "pws-spring:dev", "build": "spring",
                             "command": ["java", "-jar", "/app/app.jar"],
                             "env": {"SPRING_THREADS_VIRTUAL_ENABLED": "true", "SERVER_TOMCAT_MAX_CONNECTIONS": "1000"},
                             "probes": SPRING_PROBES},
    "node": {"name": "Node.js node:http", "image": "pws-node:dev", "build": "node",
             "command": ["node", "/app/server.mjs"], "env": {}, "probes": ("/healthz", "/healthz")},
    "go": {"name": "Go net/http", "image": "pws-go:dev", "build": "go",
           "command": ["/server"], "env": {}, "probes": ("/healthz", "/healthz")},
}
ORDER = ["gunicorn", "tomcat", "tomcat-virtual", "node", "go"]
MGMT = "tomcat-mgmt"
LIMIT = "tomcat-virtual-limit"
