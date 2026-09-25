# deployment
Deploy all troops at a corner (prefer right, left, or top corners, make sure the area is deployable) of the map, including all heroes and spells. Pick a corner closer to the townhall.
Deploy heroes first, then other troops, then spells. You should deploy eveyrthing in one go, not separate steps. Make sure all troops are actually deployed.
Also, never deploy troops or spells at the same point!!! No two things should be deploy at the same point!! Everytime you deploy a troop with click, make sure each click is followed by randomized delay between 0.15s-0.2s
If troop is dragons, spread out the dragons (make sure the deploy point doesn't collide with others)
Remember to click on the hero icon after deploy to activate the hero skills. For grand warden, activate his skill after 5 seconds (but you should do this in the same step just write a delay for grand warden)

Continously check battle screenshots to see if there are missing deployments, or actions (like hero skills, etc). Don't wait idle or use timer

When battle timer is 1min, usually on the right side of screen there is a 1X button. When this appears you should press it to speed up the battle to use 4X speed.
- but never write any background watcher that tries to press 1X button because he battle might end early.


Important: do this once (not every battle): zoom out of the battle scene in base (either home or builder base), so that all future attacks will show the full map and allow easy pick for the deployment spots

# Home base

## loot threshold
Loot threshold should only be considered for high level townhalls. For high level home base townhalls, skip townhalls using the following criteria.
- None for now


# Builder base
The best troop to 3star for now is to use all Night Witches