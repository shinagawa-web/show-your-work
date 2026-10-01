package main

import (
	"bufio"
	"net"
	"os"
	"runtime"
	"strconv"
	"syscall"
	"unsafe"
)

const clockThreadCPUTimeID = 3

var resp = []byte("HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

func threadCPU() int64 {
	var ts syscall.Timespec
	syscall.Syscall(syscall.SYS_CLOCK_GETTIME, clockThreadCPUTimeID, uintptr(unsafe.Pointer(&ts)), 0)
	return ts.Nano()
}

func burn(ns int64) {
	end := threadCPU() + ns
	x := 1.0
	for threadCPU() < end {
		for i := 0; i < 200; i++ {
			x = x*1.0000001 + 1e-9
		}
	}
	_ = x
}

func serve(c net.Conn, cpuNs int64) {
	defer c.Close()
	r := bufio.NewReader(c)
	for {
		l, err := r.ReadString('\n')
		if err != nil {
			return
		}
		if l == "\r\n" {
			break
		}
	}
	burn(cpuNs)
	c.Write(resp)
}

func main() {
	w, _ := strconv.Atoi(os.Getenv("W"))
	cpuMs, _ := strconv.ParseFloat(os.Getenv("CPU_MS"), 64)
	ln, err := net.Listen("tcp", ":8080")
	if err != nil {
		panic(err)
	}
	for i := 0; i < w; i++ {
		go func() {
			runtime.LockOSThread()
			for {
				c, err := ln.Accept()
				if err != nil {
					continue
				}
				serve(c, int64(cpuMs*1e6))
			}
		}()
	}
	select {}
}
