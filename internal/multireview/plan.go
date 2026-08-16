package multireview

import (
	"fmt"
	"strings"
)

func FreezePlan(plan FrozenPlan) (FrozenPlan, error) {
	if plan.frozen {
		return FrozenPlan{}, ErrImmutablePlan
	}
	if err := ValidateFrozenPlan(plan); err != nil {
		return FrozenPlan{}, err
	}
	snapshot := clonePlan(plan)
	snapshot.frozen = true
	return snapshot, nil
}

func (plan FrozenPlan) IsFrozen() bool {
	return plan.frozen
}

func ValidateAmendment(plan FrozenPlan, amendment ReviewAmendment) error {
	if !plan.frozen {
		return fmt.Errorf("%w: amendment target must be a frozen plan", ErrInvalidAmendment)
	}
	if err := ValidateID(amendment.AmendmentID); err != nil {
		return fmt.Errorf("%w: amendment_id: %v", ErrInvalidAmendment, err)
	}
	if amendment.PlanID != plan.PlanID || amendment.PlanVersion != plan.Version {
		return fmt.Errorf("%w: amendment scope does not match frozen plan", ErrInvalidAmendment)
	}
	if !isAllowedLocalAmendment(amendment.Operation) {
		return fmt.Errorf("%w: operation %q changes structural scope", ErrInvalidAmendment, amendment.Operation)
	}
	if len(amendment.TargetIDs) == 0 {
		return fmt.Errorf("%w: target_ids are required", ErrInvalidAmendment)
	}
	if strings.TrimSpace(amendment.Reason) == "" {
		return fmt.Errorf("%w: reason is required", ErrInvalidAmendment)
	}
	if err := validateIDList(amendment.TargetIDs, "target_ids"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidAmendment, err)
	}
	for _, targetID := range amendment.TargetIDs {
		if !planContainsID(plan, targetID) {
			return fmt.Errorf("%w: target %q is not in frozen plan", ErrInvalidAmendment, targetID)
		}
	}
	if err := validateIDList(amendment.Invalidates, "invalidates"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidAmendment, err)
	}
	return nil
}

func InvalidationScope(plan FrozenPlan, amendment ReviewAmendment) ([]ID, error) {
	if err := ValidateAmendment(plan, amendment); err != nil {
		return nil, err
	}

	affectedItems := make(map[ID]struct{})
	affectedGroups := make(map[ID]struct{})
	for _, targetID := range amendment.TargetIDs {
		if group, ok := plan.group(targetID); ok {
			affectedGroups[group.ID] = struct{}{}
			for _, item := range group.Items {
				affectedItems[item.ID] = struct{}{}
			}
			continue
		}
		if item, groupID, ok := plan.item(targetID); ok {
			affectedItems[item.ID] = struct{}{}
			affectedGroups[groupID] = struct{}{}
		}
	}

	changed := true
	for changed {
		changed = false
		for _, group := range plan.Groups {
			for _, item := range group.Items {
				if _, already := affectedItems[item.ID]; already {
					continue
				}
				if referencesAny(item.Dependencies, affectedItems) {
					affectedItems[item.ID] = struct{}{}
					affectedGroups[group.ID] = struct{}{}
					changed = true
				}
			}
		}
		for _, group := range plan.Groups {
			if _, already := affectedGroups[group.ID]; already {
				continue
			}
			if referencesAny(group.Dependencies, affectedGroups) {
				affectedGroups[group.ID] = struct{}{}
				changed = true
			}
		}
	}

	scope := make([]ID, 0, len(affectedItems)+len(affectedGroups))
	for _, group := range plan.Groups {
		for _, item := range group.Items {
			if _, ok := affectedItems[item.ID]; ok {
				scope = append(scope, item.ID)
			}
		}
		if _, ok := affectedGroups[group.ID]; ok {
			scope = append(scope, group.ID)
		}
	}
	return scope, nil
}

func isAllowedLocalAmendment(operation AmendmentOperation) bool {
	switch operation {
	case AmendmentUpdateItem, AmendmentSplitItem, AmendmentMergeItems, AmendmentRemoveItem:
		return true
	default:
		return false
	}
}

func planContainsID(plan FrozenPlan, target ID) bool {
	if _, ok := plan.group(target); ok {
		return true
	}
	_, _, ok := plan.item(target)
	return ok
}

func (plan FrozenPlan) group(target ID) (Group, bool) {
	for _, group := range plan.Groups {
		if group.ID == target {
			return group, true
		}
	}
	return Group{}, false
}

func (plan FrozenPlan) item(target ID) (Item, ID, bool) {
	for _, group := range plan.Groups {
		for _, item := range group.Items {
			if item.ID == target {
				return item, group.ID, true
			}
		}
	}
	return Item{}, "", false
}

func referencesAny(ids []ID, targets map[ID]struct{}) bool {
	for _, id := range ids {
		if _, ok := targets[id]; ok {
			return true
		}
	}
	return false
}

func clonePlan(plan FrozenPlan) FrozenPlan {
	cloned := plan
	cloned.EvidenceSnapshotIDs = append([]ID(nil), plan.EvidenceSnapshotIDs...)
	cloned.Groups = make([]Group, len(plan.Groups))
	for index, group := range plan.Groups {
		cloned.Groups[index] = group
		cloned.Groups[index].Dependencies = append([]ID(nil), group.Dependencies...)
		cloned.Groups[index].Items = make([]Item, len(group.Items))
		for itemIndex, item := range group.Items {
			cloned.Groups[index].Items[itemIndex] = item
			cloned.Groups[index].Items[itemIndex].Dependencies = append([]ID(nil), item.Dependencies...)
		}
	}
	return cloned
}
