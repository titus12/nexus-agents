package catalog

import (
	"fmt"
	"sort"
	"strings"
)

func (s *Store) ProjectGroupByID(groupID string) (ProjectGroup, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, group := range s.data.ProjectGroups {
		if group.ID == groupID {
			return cloneProjectGroups([]ProjectGroup{group})[0], true
		}
	}
	return ProjectGroup{}, false
}

func (s *Store) ProjectGroupsForProject(projectID string) ([]ProjectGroup, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if _, ok := s.projectByIDLocked(projectID); !ok {
		return nil, false
	}
	return projectGroupsForProject(s.data.ProjectGroups, projectID), true
}

func (s *Store) CreateProjectGroup(input ProjectGroupInput) (ProjectGroup, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	name := strings.TrimSpace(input.Name)
	if name == "" {
		return ProjectGroup{}, fmt.Errorf("project group name is required")
	}
	for _, group := range s.data.ProjectGroups {
		if strings.EqualFold(group.Name, name) {
			return ProjectGroup{}, fmt.Errorf("project group name already exists")
		}
	}
	projectIDs, err := s.validateProjectIDsLocked(input.ProjectIDs)
	if err != nil {
		return ProjectGroup{}, err
	}
	existing := map[string]bool{}
	for _, group := range s.data.ProjectGroups {
		existing[group.ID] = true
	}
	group := ProjectGroup{
		ID: uniqueID(slugify(name), existing), Name: name, ProjectIDs: projectIDs,
	}
	groups := append(cloneProjectGroups(s.data.ProjectGroups), group)
	if err := persistUserProjectGroups(groups); err != nil {
		return ProjectGroup{}, err
	}
	s.data.ProjectGroups = groups
	return cloneProjectGroups([]ProjectGroup{group})[0], nil
}

func (s *Store) UpdateProjectGroup(groupID string, input ProjectGroupInput) (ProjectGroup, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	groups := cloneProjectGroups(s.data.ProjectGroups)
	for index := range groups {
		if groups[index].ID != groupID {
			continue
		}
		if name := strings.TrimSpace(input.Name); name != "" {
			for _, other := range groups {
				if other.ID != groupID && strings.EqualFold(other.Name, name) {
					return ProjectGroup{}, true, fmt.Errorf("project group name already exists")
				}
			}
			groups[index].Name = name
		}
		if input.ProjectIDs != nil {
			projectIDs, err := s.validateProjectIDsLocked(input.ProjectIDs)
			if err != nil {
				return ProjectGroup{}, true, err
			}
			groups[index].ProjectIDs = projectIDs
		}
		if err := persistUserProjectGroups(groups); err != nil {
			return ProjectGroup{}, true, err
		}
		s.data.ProjectGroups = groups
		return cloneProjectGroups([]ProjectGroup{groups[index]})[0], true, nil
	}
	return ProjectGroup{}, false, nil
}

func (s *Store) DeleteProjectGroup(groupID string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	groups := cloneProjectGroups(s.data.ProjectGroups)
	for index, group := range groups {
		if group.ID != groupID {
			continue
		}
		groups = append(groups[:index], groups[index+1:]...)
		if err := persistUserProjectGroups(groups); err != nil {
			return true, err
		}
		s.data.ProjectGroups = groups
		return true, nil
	}
	return false, nil
}

func (s *Store) SetProjectGroups(projectID string, groupIDs []string) ([]ProjectGroup, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.projectByIDLocked(projectID); !ok {
		return nil, false, nil
	}
	if err := validateProjectGroupIDs(groupIDs, s.data.ProjectGroups); err != nil {
		return nil, true, err
	}
	groups := groupsWithProjectMembership(s.data.ProjectGroups, projectID, groupIDs)
	if err := persistUserProjectGroups(groups); err != nil {
		return nil, true, err
	}
	s.data.ProjectGroups = groups
	return projectGroupsForProject(groups, projectID), true, nil
}

func (s *Store) ProjectsForGroup(groupID string) ([]Project, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var projectIDs []string
	found := false
	for _, group := range s.data.ProjectGroups {
		if group.ID == groupID {
			projectIDs = group.ProjectIDs
			found = true
			break
		}
	}
	if !found {
		return nil, false
	}
	byID := make(map[string]Project, len(s.data.Projects))
	for _, project := range s.data.Projects {
		byID[project.ID] = project
	}
	projects := make([]Project, 0, len(projectIDs))
	for _, projectID := range projectIDs {
		if project, ok := byID[projectID]; ok {
			projects = append(projects, project)
		}
	}
	return cloneProjects(projects), true
}

func (s *Store) validateProjectIDsLocked(values []string) ([]string, error) {
	known := map[string]bool{}
	for _, project := range s.data.Projects {
		known[project.ID] = true
	}
	result := uniqueProjectIDs(values)
	for _, projectID := range result {
		if !known[projectID] {
			return nil, fmt.Errorf("unknown project id %s", projectID)
		}
	}
	return result, nil
}

func validateProjectGroupIDs(values []string, groups []ProjectGroup) error {
	known := map[string]bool{}
	for _, group := range groups {
		known[group.ID] = true
	}
	for _, groupID := range uniqueProjectIDs(values) {
		if !known[groupID] {
			return fmt.Errorf("unknown project group id %s", groupID)
		}
	}
	return nil
}

func groupsWithProjectMembership(groups []ProjectGroup, projectID string, groupIDs []string) []ProjectGroup {
	selected := map[string]bool{}
	for _, groupID := range uniqueProjectIDs(groupIDs) {
		selected[groupID] = true
	}
	result := cloneProjectGroups(groups)
	for index := range result {
		filtered := make([]string, 0, len(result[index].ProjectIDs)+1)
		for _, existingProjectID := range result[index].ProjectIDs {
			if existingProjectID != projectID {
				filtered = append(filtered, existingProjectID)
			}
		}
		if selected[result[index].ID] {
			filtered = append(filtered, projectID)
		}
		result[index].ProjectIDs = uniqueProjectIDs(filtered)
	}
	return result
}

func projectGroupsForProject(groups []ProjectGroup, projectID string) []ProjectGroup {
	result := make([]ProjectGroup, 0)
	for _, group := range groups {
		for _, memberID := range group.ProjectIDs {
			if memberID == projectID {
				result = append(result, group)
				break
			}
		}
	}
	return cloneProjectGroups(result)
}

func uniqueProjectIDs(values []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
