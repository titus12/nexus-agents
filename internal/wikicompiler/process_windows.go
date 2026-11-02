//go:build windows

package wikicompiler

import (
	"os/exec"
	"strconv"
)

func configureProcess(cmd *exec.Cmd) {
	// taskkill /T walks the process tree by parent PID; no process group is required.
}

func killProcessTree(cmd *exec.Cmd) error {
	if cmd == nil || cmd.Process == nil {
		return nil
	}
	// The npm openwiki shim is cmd.exe -> node.exe. Killing only the direct
	// child leaves node.exe alive and keeps the inherited stdout/stderr pipes
	// open, which makes Cmd.Wait block long after the context deadline.
	killer := exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid))
	// A failure here normally means the process already exited; Wait/ctx
	// cancellation still reports the timeout, so do not mask it.
	_ = killer.Run()
	return nil
}
