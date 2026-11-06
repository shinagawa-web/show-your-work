package example;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.util.Locale;
import java.util.concurrent.Executor;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicLong;
import org.apache.catalina.startup.Tomcat;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.ApplicationContext;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@SpringBootApplication
@RestController
public class App implements ApplicationRunner {
    static final Semaphore SEM = new Semaphore(4, true);
    static final AtomicLong ARRIVED = new AtomicLong();
    static final AtomicLong DONE = new AtomicLong();
    static final AtomicLong INFLIGHT = new AtomicLong();
    static final Duration WORK = Duration.ofNanos((long) (Double.parseDouble(System.getenv().getOrDefault("WORK_MS", "20")) * 1_000_000));

    private final ApplicationContext ctx;
    private final ObjectProvider<MeterRegistry> registry;

    App(ApplicationContext ctx, ObjectProvider<MeterRegistry> registry) {
        this.ctx = ctx;
        this.registry = registry;
    }

    public static void main(String[] args) {
        SpringApplication.run(App.class, args);
    }

    @GetMapping("/work")
    public String work() throws InterruptedException {
        ARRIVED.incrementAndGet();
        INFLIGHT.incrementAndGet();
        SEM.acquireUninterruptibly();
        try {
            Thread.sleep(WORK);
        } finally {
            SEM.release();
        }
        DONE.incrementAndGet();
        INFLIGHT.decrementAndGet();
        return "ok\n";
    }

    @Override
    public void run(ApplicationArguments args) throws Exception {
        Object webServer = ctx.getClass().getMethod("getWebServer").invoke(ctx);
        Tomcat tomcat = (Tomcat) webServer.getClass().getMethod("getTomcat").invoke(webServer);
        Executor executor = tomcat.getConnector().getProtocolHandler().getExecutor();
        Thread.ofPlatform().daemon().name("stats").start(() -> loop(executor));
    }

    private String gauge(String name) {
        MeterRegistry r = registry.getIfAvailable();
        Gauge g = r == null ? null : r.find(name).gauge();
        return g == null ? "NA" : String.format(Locale.ROOT, "%.0f", g.value());
    }

    private void loop(Executor executor) {
        long pid = ProcessHandle.current().pid();
        double prev = System.currentTimeMillis() / 1000.0;
        while (true) {
            try {
                Thread.sleep(1000 - System.currentTimeMillis() % 1000);
            } catch (InterruptedException e) {
                return;
            }
            double now = System.currentTimeMillis() / 1000.0;
            String queue = "NA";
            String pool = "NA";
            if (executor instanceof org.apache.tomcat.util.threads.ThreadPoolExecutor tpe) {
                queue = Integer.toString(tpe.getQueue().size());
                pool = Integer.toString(tpe.getPoolSize());
            }
            System.out.printf(Locale.ROOT, "stat %.3f %.3f %d %d %d %d %d busy=%s conns=%s queue=%s pool=%s executor=%s%n",
                    prev, now, pid, ARRIVED.getAndSet(0), DONE.getAndSet(0), SEM.getQueueLength(), INFLIGHT.get(),
                    gauge("tomcat.threads.busy"), gauge("tomcat.connections.current"), queue, pool,
                    executor.getClass().getSimpleName());
            prev = now;
        }
    }
}
