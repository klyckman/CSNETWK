# Creature combat

Combat is an authoritative server sub-state machine. The phase sequence is
Beginning of Combat, Declare Attackers, Declare Blockers, optional Assign
Damage Order, optional First Strike Damage, Combat Damage, and End of Combat.
Attacker, blocker, and order decisions echo the personalized sequence number
from the corresponding `PHASE_TRANSITION`.

## Attackers

The Active Player may declare any number of controlled, untapped creatures that
are not summoning sick and do not have Defender. Every attacker targets the
opposing player. Validation is atomic: if one entry is illegal, no creature is
tapped or marked as attacking. Legal attackers tap immediately unless they have
Vigilance. An empty declaration skips directly to End of Combat.

After a non-empty declaration, the server publishes the tapped battlefield and
opens a priority window. Spells may therefore remove or modify combatants before
blockers are declared.

## Blockers and order

The defending player assigns controlled, untapped creatures. Each blocker may
block one attacker, while an attacker may receive several blockers. Flying
attackers require a flying blocker because the supplied catalog has no Reach.
A creature cannot block an attacker protected from that blocker's color.
Blocking does not tap the blocker and summoning sickness does not prevent
blocking.

If an attacker has two or more living blockers after the blocker priority
window, the attacking player orders those blockers. During damage, lethal
damage must be assigned to each earlier blocker before damage is assigned to
the next. MTGNP explicitly disables trample, so a blocked attacker never deals
excess damage to the defending player.

## Damage

First strike is a separate damage step when any living combatant has First
Strike or Double Strike. State-based actions remove lethally damaged creatures
before the regular step. Regular combat damage is simultaneous: unblocked
attackers damage the defending player, blocked attackers damage blockers in
order, and every surviving blocker damages the creature it blocked. Protection
prevents damage from sources of the protected color.

Each damage step broadcasts `COMBAT_DAMAGE_RESULT` with individual damage
events, current life totals, and creature IDs moved to graveyards. Life at zero
or less ends the game immediately with `LIFE_ZERO`. Combat assignments are
cleared when End of Combat advances to the Postcombat Main Phase; marked damage
remains until Cleanup.

This milestone deliberately follows the RFC rule that trample is disabled.
Activated combat abilities, banding-style effects, regeneration decisions, and
trigger ordering remain later work.
