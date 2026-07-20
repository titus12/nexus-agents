//go:build windows

package catalog

import (
	"os/exec"
	"syscall"
)

// configureHiddenCommand prevents transient cmd.exe windows when checking
// infrastructure installed through npm, pnpm, or Bun command shims.
func configureHiddenCommand(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
}
