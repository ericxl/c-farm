Use scrcpy to play Clash of Clans

# Interactions
if scrcpy is not launched, use `adb shell wm size 540x1200 && adb shell wm density 210 && scrcpy --stay-awake`.
    - `wm size 540x1200` + `wm density 210` set the phone's logical display to half resolution, so `adb exec-out screencap -p` returns 1200x540 and `adb shell input tap/swipe` use those same halved coordinates. No coordinate scaling needed.
    - The wm override persists across game restarts; to go back to full res run `adb shell wm size reset && adb shell wm density reset`.

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
Read @battle.md

# Builders
With low level townhalls you need to build or upgrade buildings or troops and keep all builders busy at all times.
Prefer using the builder icon on top rather than Shop icon. When upgrading walls, prefer using the builder icon to pick what walls to upgrade (this is so that it is easier to pick which wall to upgrade based on the level )
- When upgrading walls, prefer to upgrade the lower level walls first (that costs lowest resources)
- Tip: after selecting one wall, use "Upgrade More" button to select more walls of the same level
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


# Other docs
@clan-games-challenges.md: for completing clan games challenges
