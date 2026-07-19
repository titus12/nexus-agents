package catalog

import (
	"fmt"
	"strings"
	"testing"
)

func TestOpenWikiInfrastructureIsPinned(t *testing.T) {
	var calls []string
	service := NewInfrastructureService(InfrastructureServiceOptions{
		Runner: InfrastructureCommandRunnerFunc(func(name string, args ...string) (string, error) {
			calls = append(calls, strings.Join(append([]string{name}, args...), " "))
			if name == "openwiki" && len(args) == 1 && args[0] == "--help" {
				return "openwiki v0.2.0", nil
			}
			if name == "npm" {
				return "installed", nil
			}
			return "", fmt.Errorf("missing")
		}),
	})
	item, ok, err := service.Check("openwiki")
	if err != nil || !ok {
		t.Fatalf("check = %#v, %v, %v", item, ok, err)
	}
	if item.Status != "ready" || item.Version != "0.2.0" {
		t.Fatalf("item = %#v", item)
	}
	if _, _, err := service.Install("openwiki"); err != nil {
		t.Fatal(err)
	}
	foundPinnedInstall := false
	for _, call := range calls {
		if call == "npm install --global openwiki@0.2.0" {
			foundPinnedInstall = true
		}
	}
	if !foundPinnedInstall {
		t.Fatalf("calls = %#v", calls)
	}
}
