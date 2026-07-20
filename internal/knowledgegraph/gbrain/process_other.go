//go:build !windows

package gbrain

import "os/exec"

func configureHiddenProcess(*exec.Cmd) {}
