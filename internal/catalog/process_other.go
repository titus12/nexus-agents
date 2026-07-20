//go:build !windows

package catalog

import "os/exec"

func configureHiddenCommand(*exec.Cmd) {}
