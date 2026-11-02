//go:build !windows

package wikicompiler

import (
	"os/exec"
	"syscall"
)

func configureProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func killProcessTree(cmd *exec.Cmd) error {
	if cmd == nil || cmd.Process == nil {
		return nil
	}
	// Negative PID targets the process group created by Setpgid.
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	return nil
}
