Feature: Relay Instagram DMs into Discord threads
  When someone DMs the group's Instagram account, Dobby opens a thread in the DM channel so a
  teammate can answer from Discord. A reply is previewed, sent only after the requester's
  Confirm, and then the thread is closed. Only IDs and cursors are stored, never message text.

  Background:
    Given the Instagram DM relay posts to channel 900
    And Dobby started at "2026-11-22T10:00:00+00:00"

  Scenario: A new DM opens a thread
    Given "maya.designs" sends "Can we collab on a story?" at "10:05" in conversation "c1"
    When Dobby polls Instagram
    Then a thread named "Instagram DM: @maya.designs" is opened in channel 900
    And the thread for "c1" shows "Can we collab on a story?"

  Scenario: A second DM in the same conversation reuses the open thread
    Given "maya.designs" sends "Hi!" at "10:05" in conversation "c1"
    And Dobby polls Instagram
    When "maya.designs" sends "Are you there?" at "10:06" in conversation "c1"
    And Dobby polls Instagram
    Then 1 thread has been opened
    And the thread for "c1" shows "Are you there?"
    And the thread for "c1" shows "Hi!" exactly once

  Scenario: Our own outgoing messages are not relayed
    Given "maya.designs" sends "Hi!" at "10:05" in conversation "c1"
    And Dobby polls Instagram
    When our account sends "Thanks, we will get back to you" at "10:07" in conversation "c1"
    And Dobby polls Instagram
    Then the thread for "c1" does not show "Thanks, we will get back to you"

  Scenario: Startup does not relay the existing inbox
    Given "old.friend" sends "Hello from last week" at "2026-11-15T09:00:00+00:00" in conversation "c9"
    When Dobby polls Instagram
    Then 0 threads have been opened
    When "old.friend" sends "Back again!" at "10:10" in conversation "c9"
    And Dobby polls Instagram
    Then the thread for "c9" shows "Back again!"
    And the thread for "c9" does not show "Hello from last week"

  Scenario: A reply waits for Confirm, and only the requester can confirm
    Given an open DM thread from "maya.designs" in conversation "c1"
    When teammate 1 runs "/dm reply" with "Yes! Send us the details." in the thread for "c1"
    Then Dobby previews "Yes! Send us the details." with Confirm and Cancel
    And nothing has been sent on Instagram
    When teammate 2 presses Confirm
    Then teammate 2 is told only the requester can confirm
    And nothing has been sent on Instagram

  Scenario: A confirmed reply sends the exact text and closes the thread
    Given an open DM thread from "maya.designs" in conversation "c1"
    When teammate 1 runs "/dm reply" with "Yes!  See you *Friday* @everyone" in the thread for "c1"
    And teammate 1 presses Confirm
    Then Instagram received exactly "Yes!  See you *Friday* @everyone" for "maya.designs"
    And Dobby reports the "Instagram DM reply" was published
    And the thread for "c1" is archived
    And conversation "c1" is closed

  Scenario: Cancelling a reply leaves the thread open
    Given an open DM thread from "maya.designs" in conversation "c1"
    When teammate 1 runs "/dm reply" with "No thanks" in the thread for "c1"
    And teammate 1 presses Cancel
    Then nothing has been sent on Instagram
    And the thread for "c1" is not archived
    And conversation "c1" is open

  Scenario: Closing a thread without replying
    Given an open DM thread from "maya.designs" in conversation "c1"
    When teammate 1 runs "/dm close" in the thread for "c1"
    Then Dobby says the thread is closed
    And the thread for "c1" is archived
    And conversation "c1" is closed
    And nothing has been sent on Instagram
    When "maya.designs" sends "Hello again" at "10:20" in conversation "c1"
    And Dobby polls Instagram
    Then 2 threads have been opened

  Scenario: Mentioning Dobby in the thread queues the same confirm-gated reply
    Given an open DM thread from "maya.designs" in conversation "c1"
    When the model drafts the reply "Sounds great!" in the thread for "c1"
    Then the reply "Sounds great!" is queued for confirmation
    And nothing has been sent on Instagram
    When the model drafts the reply "Sounds great!" outside any DM thread
    Then the model is told it can only reply inside a DM thread

  Scenario: Teammates outside the allowlist cannot reply
    Given an open DM thread from "maya.designs" in conversation "c1"
    When teammate 3 runs "/dm reply" with "Hi" in the thread for "c1"
    Then teammate 3 is told they are not authorized
    And nothing has been sent on Instagram

  Scenario: The relay is off when no channel is configured
    Given INSTAGRAM_DM_CHANNEL_ID is unset
    Then Dobby does not run the DM relay
    And the /dm command is not offered

  Scenario: A Composio failure does not crash the poller
    Given Instagram fails to answer
    When Dobby polls Instagram
    Then 0 threads have been opened
    And the background poller keeps running after an unexpected error
    When Instagram recovers
    And "maya.designs" sends "Anyone there?" at "10:30" in conversation "c1"
    And Dobby polls Instagram
    Then the thread for "c1" shows "Anyone there?"

  Scenario: No message text is persisted
    Given "maya.designs" sends "My number is 555-0100" at "10:05" in conversation "c1"
    And Dobby polls Instagram
    When teammate 1 runs "/dm reply" with "We will call 555-0199" in the thread for "c1"
    And teammate 1 presses Confirm
    Then no stored value or audit record contains "555-0100"
    And no stored value or audit record contains "555-0199"
    And no stored value or audit record contains "maya.designs"
