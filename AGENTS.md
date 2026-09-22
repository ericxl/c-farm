Use scrcpy to play Clash of Clans

# Interactions
if scrcpy is not launched, use `adb shell wm size 540x1200 && adb shell wm density 210 && open -n ~/Applications/scrcpy.app --args --stay-awake`.
    - `wm size 540x1200` + `wm density 210` set the phone's logical display to half resolution, so `adb exec-out screencap -p` returns 1200x540 and `adb shell input tap/swipe` use those same halved coordinates. No coordinate scaling needed.
    - The wm override persists across game restarts; to go back to full res run `adb shell wm size reset && adb shell wm density reset`.
    - must be launched via a wrapper app in ~/Applications/scrcpy.app, otherwise computer-use tool can't find the window.

use screenshot, click, and drag from adb

# COC accounts
- PleasingEricx (Eirikr): main account
- CherishedEricx2 (ericx2): second account
To switch account, press the gear icon, then press the refresh button on the left of "Open"

# Current objective
Our resources are maxed, our objective is to attack bases and complete achivements
Currently gold, elixir, and dark elixir are useless (wall are maxed, all builders are busy). So goal is to:
1. Try to advance these achievements:
    - Win multiplayer battles
    - Destory inferno towers, weaponized THs, Eagle or inferno artiliery, Scattershots, Spell towers, monoliths, crafted defenses.

# Battle

## loot threshold
Loot threshold should only be considered for high level townhalls. For high level home base townhalls, skip townhalls using the following criteria.
- None for now

## deployment
Deploy all troops at a corner (prefer right, left, or top corners, make sure the area is deployable) of the map, including all heroes and spells. Pick a corner closer to the townhall.
Deploy heroes first, then other troops, then spells. You should deploy eveyrthing in one go, not separate steps. Make sure all troops are actually deployed.
Also, never deploy troops or spells at the same point!!! No two things should be deploy at the same point!! Everytime you deploy a troop with click, make sure each click is followed by randomized delay between 0.15s-0.2s
If troop is dragons, spread out the dragons (make sure the deploy point doesn't collide with others)
Remember to click on the hero icon after deploy to activate the hero skills. For grand warden, activate his skill after 5 seconds (but you should do this in the same step just write a delay for grand warden)

Continously check battle screenshots to see if there are missing deployments, or actions (like hero skills, etc). Don't wait idle or use timer

When battle timer is 1min, usually on the right side of screen there is a 1X button. When this appears you should press it to speed up the battle to use 4X speed.
- but never write any background watcher that tries to press 1X button because he battle might end early.


# Builders
With low level townhalls you need to build or upgrade buildings or troops and keep all builders busy at all times.
Prefer using the builder icon on top rather than Shop icon. When upgrading walls, prefer using the builder icon to pick what walls to upgrade (this is so that it is easier to pick which wall to upgrade based on the level )
- When upgrading walls, prefer to upgrade the lower level walls first
When resources support upgrading multiple walls, after tapping on a wall you could utilize "Upgrade More" button when a wall is selected 

# Builder base
- IMPORTANT: After each builder base battle, remember to claim from the elixir cart (in the builder base on top next to the return-to-home-base ship). Sometimes you have to scroll to top of the builder base to see it.
- rememebr for builder base there are 2 stages. so don't write a single script to battle all attacks but look at each base separately
- If clock tower is available for boost, do it.

# Rules
- Don't use gems unless approved by me.
- Never claim achivement gem rewards.
- use tmp directories for screenshots, etc. never save to this repo

## Reconnect rules
If connection is lost because another device is connecting to the village, do not stop the task. Simply call a script to wait for 5min and then reload and continue!

## Priority
Top priority for low level townhalls is to advance to next TH as soon as possible
1. Build (not upgrade) all buildings avaialble for the TH
1. Upgrade all required buildings to advance to next townhall
2. Buildings & upgrades that improves attack power
    - Research lab,
    - Pet house, barracks, dark barracks, army camps, lab upgrades, spell (and dark) factory

Deprioritize all resource collectors upgrades. Upgrade resource storage only such that it is enough to advance to next TH

### Troops priority
- healers, dragons, goblins, wallbreakers,

### Hero pet priority
1. Pheonix
2. Sneezy
3. Spirit Fox
4. Frosty
5. Unicorn
6. Angry Jelly
7. Greedy Raven
8. Poison Wizard
9. Diggy
10. Electro Owl
11. Yak
12. LASSI