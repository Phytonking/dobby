Feature: Admins import many teammates at once
  An admin uploads a CSV (a sign-up sheet or a Google Contacts export), previews
  what will happen, then imports. Existing people are never changed.

  Background:
    Given I am signed in as an admin

  Scenario: Previewing an import saves nothing
    Given a CSV:
      """
      name,email,discord_id,calendar_email,role
      Jane Doe,Jane@Gmail.com,,,student
      Sam Lee,,123456789012345678,sam@uw.edu,admin
      """
    When I preview the import
    Then 2 people would be added
    And nothing is saved

  Scenario: Importing adds everyone who is valid and new
    Given a CSV:
      """
      name,email,discord_id,calendar_email,role
      Jane Doe,Jane@Gmail.com,,,student
      Sam Lee,,123456789012345678,sam@uw.edu,admin
      """
    When I run the import
    Then 2 people are added
    And "Jane Doe" is added with Google email "jane@gmail.com" and calendar email "jane@gmail.com"
    And "Sam Lee" is added as an "admin" with calendar email "sam@uw.edu"

  Scenario: People already on the roster are skipped, not changed
    Given a CSV:
      """
      name,email
      Jane Doe,jane@gmail.com
      New Person,new@example.org
      """
    And "jane@gmail.com" is already on the roster
    When I run the import
    Then 1 person is added
    And line 2 is "skipped" because "already on the roster"

  Scenario: Problem rows are reported and left out
    Given a CSV:
      """
      name,email,discord_id,role
      Bad Email,not-an-email,,student
      ,someone@example.org,,student
      No Contact,,,student
      Bad Role,role@example.org,,owner
      Bad Discord,,sam#1234,student
      Good One,good@example.org,,student
      """
    When I run the import
    Then 1 person is added
    And line 2 is "invalid" because "valid email address"
    And line 3 is "invalid" because "display name"
    And line 4 is "invalid" because "email or a Discord ID"
    And line 5 is "invalid" because "admin or student"
    And line 6 is "invalid" because "numeric Discord user ID"

  Scenario: The same person twice in one file is added once
    Given a CSV:
      """
      name,email
      Jane Doe,jane@gmail.com
      Jane Again,JANE@gmail.com
      """
    When I run the import
    Then 1 person is added
    And line 3 is "skipped" because "listed twice"

  Scenario: A Google Contacts export is understood
    Given a CSV:
      """
      First Name,Last Name,E-mail 1 - Value
      Ada,Lovelace,ada@example.org
      """
    When I run the import
    Then 1 person is added
    And "Ada Lovelace" is added with Google email "ada@example.org" and calendar email "ada@example.org"

  Scenario: A CSV without the needed columns is refused
    Given a CSV:
      """
      phone,address
      555,Somewhere
      """
    When I run the import
    Then the request is refused with 422
    And the reason mentions "name column"

  Scenario: Students cannot import
    Given I am signed in as a student instead
    And a CSV:
      """
      name,email
      Jane,jane@gmail.com
      """
    When I run the import
    Then the request is refused with 403
