package knowledgesync

import (
	"context"
	"sync"
	"time"
)

type ScheduledProject struct {
	ProjectID   string
	ProjectRoot string
}

type ScheduledProjectProvider interface {
	KnowledgeSyncProjects() []ScheduledProject
}

type Scheduler struct {
	Service  *Service
	Projects ScheduledProjectProvider
	Interval time.Duration
	cancel   context.CancelFunc
	wg       sync.WaitGroup
}

func (s *Scheduler) Start(parent context.Context) {
	if s == nil || s.Service == nil || s.Projects == nil || s.cancel != nil {
		return
	}
	interval := s.Interval
	if interval <= 0 {
		interval = time.Minute
	}
	ctx, cancel := context.WithCancel(parent)
	s.cancel = cancel
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				s.check(ctx)
			}
		}
	}()
}

func (s *Scheduler) Stop() {
	if s == nil || s.cancel == nil {
		return
	}
	s.cancel()
	s.wg.Wait()
	s.cancel = nil
}

func (s *Scheduler) check(ctx context.Context) {
	for _, project := range s.Projects.KnowledgeSyncProjects() {
		profile, err := LoadProfile(project.ProjectRoot)
		if err != nil || !profile.Schedule.Enabled {
			continue
		}
		state, stateErr := s.Service.Status(project.ProjectID)
		if stateErr == nil && state.LastCheckedAt != "" {
			lastChecked, parseErr := time.Parse(time.RFC3339, state.LastCheckedAt)
			if parseErr == nil && time.Since(lastChecked) < time.Duration(profile.Schedule.IntervalMinutes)*time.Minute {
				continue
			}
		}
		_, _ = s.Service.CheckUpdates(ctx, ProjectRequest{ProjectID: project.ProjectID, ProjectRoot: project.ProjectRoot})
	}
}
