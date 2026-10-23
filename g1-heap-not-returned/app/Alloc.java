import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;

public class Alloc {
    static byte[][] ring;
    static int lifetimeMs;
    static volatile long allocated;
    static volatile String phase = "start";
    static final long T0 = System.nanoTime() - ManagementFactory.getRuntimeMXBean().getUptime() * 1_000_000L;

    static double now() {
        return (System.nanoTime() - T0) / 1e9;
    }

    public static void main(String[] a) throws Exception {
        double peakRate = Double.parseDouble(a[0]);
        int peakSec = Integer.parseInt(a[1]);
        double afterRate = Double.parseDouble(a[2]);
        int afterSec = Integer.parseInt(a[3]);
        int objSize = Integer.parseInt(a[4]);
        lifetimeMs = Integer.parseInt(a[5]);
        Thread s = new Thread(Alloc::sample);
        s.setDaemon(true);
        s.start();
        phase = "peak";
        System.out.printf("phase peak %.3f rate_mb_s=%.0f%n", now(), peakRate);
        run(peakRate, peakSec, objSize);
        phase = "after";
        System.out.printf("phase after %.3f rate_mb_s=%.0f%n", now(), afterRate);
        run(afterRate, afterSec, objSize);
        System.out.printf("phase end %.3f%n", now());
        long sum = 0;
        for (byte[] b : ring) if (b != null) sum += b.length;
        System.out.println("ring_bytes " + sum);
    }

    static void run(double rateMBps, int sec, int objSize) throws InterruptedException {
        long start = System.nanoTime();
        long end = start + sec * 1_000_000_000L;
        long done = 0;
        int i = 0;
        double perNs = rateMBps * 1048576 / 1e9;
        if (ring != null) java.util.Arrays.fill(ring, null);
        ring = new byte[Math.max(1, (int) Math.ceil(rateMBps * 1048576 * lifetimeMs / 1000 / objSize))][];
        System.out.printf("ring_slots %d live_mb=%.1f%n", ring.length, ring.length * (double) objSize / 1048576);
        while (true) {
            long t = System.nanoTime();
            if (t >= end) break;
            long target = (long) ((t - start) * perNs);
            while (done < target) {
                ring[i] = new byte[objSize];
                i = (i + 1) % ring.length;
                done += objSize;
            }
            allocatedAdd(done);
            Thread.sleep(1);
        }
        allocatedAdd(done);
        base += done;
    }

    static long base;

    static void allocatedAdd(long done) {
        allocated = base + done;
    }

    static void sample() {
        long last = 0;
        while (true) {
            long heapCommitted = 0, heapUsed = 0;
            StringBuilder pools = new StringBuilder();
            for (MemoryPoolMXBean p : ManagementFactory.getMemoryPoolMXBeans()) {
                if (p.getType() != MemoryType.HEAP) continue;
                long c = p.getUsage().getCommitted();
                heapCommitted += c;
                heapUsed += p.getUsage().getUsed();
                pools.append(String.format(" \"%s\"=%.1f", p.getName(), c / 1048576.0));
            }
            long al = allocated;
            System.out.printf("mx %.1f %s alloc_mb=%.1f heap_committed_mb=%.1f heap_used_mb=%.1f pools_committed_mb:%s%n",
                    now(), phase, (al - last) / 1048576.0, heapCommitted / 1048576.0, heapUsed / 1048576.0, pools);
            last = al;
            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }
}
