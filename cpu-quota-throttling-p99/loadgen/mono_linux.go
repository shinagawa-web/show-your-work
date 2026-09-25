package main

import (
	"syscall"
	"unsafe"
)

const clockMonotonic = 1

func monoNow() int64 {
	var ts syscall.Timespec
	syscall.Syscall(syscall.SYS_CLOCK_GETTIME, clockMonotonic, uintptr(unsafe.Pointer(&ts)), 0)
	return ts.Nano()
}
