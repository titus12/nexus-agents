package catalog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestProjectGroupsPersistAndAllowSharedProjectMembership(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)

	store := NewStoreFromData(BootstrapData{ProjectConfigSets: map[string][]ProjectCopy{}}, nil, nil)
	groupA, err := store.CreateProjectGroup(ProjectGroupInput{Name: "Project A"})
	if err != nil {
		t.Fatal(err)
	}
	groupB, err := store.CreateProjectGroup(ProjectGroupInput{Name: "Project B"})
	if err != nil {
		t.Fatal(err)
	}

	projectRoot := t.TempDir()
	sharedRoot := t.TempDir()
	project, err := store.ImportProject(ProjectInput{Name: "A1", Path: projectRoot, GroupIDs: []string{groupA.ID}})
	if err != nil {
		t.Fatal(err)
	}
	shared, err := store.ImportProject(ProjectInput{
		Name: "CommonAuth", Path: sharedRoot, GroupIDs: []string{groupA.ID, groupB.ID},
	})
	if err != nil {
		t.Fatal(err)
	}
	groups := store.ProjectGroups()
	if len(groups) != 2 {
		t.Fatalf("groups = %#v", groups)
	}
	if got, ok := store.ProjectsForGroup(groupA.ID); !ok || len(got) != 2 {
		t.Fatalf("group A projects = %#v ok=%v", got, ok)
	}
	if got, ok := store.ProjectsForGroup(groupB.ID); !ok || len(got) != 1 || got[0].ID != shared.ID {
		t.Fatalf("group B projects = %#v ok=%v", got, ok)
	}

	data, err := os.ReadFile(filepath.Join(home, ".nexus", "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	var persisted UserProjectIndex
	if err := json.Unmarshal(data, &persisted); err != nil {
		t.Fatal(err)
	}
	if persisted.Version != 2 || len(persisted.ProjectGroups) != 2 {
		t.Fatalf("persisted index = %#v", persisted)
	}

	restored := NewStore()
	if groups := restored.ProjectGroups(); len(groups) != 2 {
		t.Fatalf("restored groups = %#v", groups)
	}
	if groups, ok := restored.ProjectGroupsForProject(shared.ID); !ok || len(groups) != 2 {
		t.Fatalf("shared memberships = %#v ok=%v", groups, ok)
	}

	if ok, err := restored.DeleteProject(project.ID); err != nil || !ok {
		t.Fatalf("delete project ok=%v err=%v", ok, err)
	}
	if projects, ok := restored.ProjectsForGroup(groupA.ID); !ok || len(projects) != 1 || projects[0].ID != shared.ID {
		t.Fatalf("group A after delete = %#v ok=%v", projects, ok)
	}
}

func TestProjectGroupMembershipRejectsUnknownGroups(t *testing.T) {
	store := NewStoreFromData(BootstrapData{
		Projects:          []Project{{ID: "sample", Name: "sample"}},
		ProjectConfigSets: map[string][]ProjectCopy{"sample": {}},
	}, nil, nil)
	if _, _, err := store.SetProjectGroups("sample", []string{"missing"}); err == nil {
		t.Fatal("expected unknown project group error")
	}
}
