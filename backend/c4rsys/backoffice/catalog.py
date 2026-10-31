"""System permissions and the three starter sets of the system staff (ТЗ § 24.1.5–24.1.6).

The codes are the technical form of § 24.1.5 and § 24.2–24.4. Which starter set holds which code
is a PROPOSAL where the ТЗ does not name it (marked below). A programmer or another technical
specialist gets nothing by his position: a platform administrator makes a set with concrete
permissions for him (§ 24.1.6).
"""

PERMISSIONS: tuple[str, ...] = (
    # Accounts of the system staff and their sets (§ 24.1.5: the platform administrator).
    "staff.manage",
    # The system journal: viewing and export are separate rights (§ 24.4.5).
    "journal.view",
    "journal.export",
    # Search and minimal view of users and companies (§ 24.1.2).
    "directory.search",
    # A logged view of the masked contacts (§ 24.4.3).
    "contacts.reveal",
    # Support tickets (§ 24.1.7).
    "tickets.manage",
    # Privileged sessions (§ 24.3.1): view by default; finances, documents and file download
    # are separate permissions.
    "privileged.open",
    "privileged.finance",
    "privileged.documents",
    "privileged.files",
    # Deferral of the notice after a privileged session (§ 24.3.2).
    "privileged.notice_defer",
    # System blocks (§ 24.2.2) and the deferral of their notice.
    "block.manage",
    "block.notice_defer",
    # The merge and the split of two global accounts: a separate permission (§ 24.1.8).
    "accounts.merge",
    # Verification of links and disputed records (§ 24.1.5, § 24.1.7): decisions on disputes of
    # ownership, corrections of system links, unlinking a disputed contact.
    "verification.resolve",
    # Recovery of access through the support (§ 2.3.1): keeping the requests and proposing a
    # decision; the confirmation by an authorized member is a separate permission.
    "recovery.manage",
    "recovery.approve",
)

PLATFORM_ADMIN = "platform_admin"
SUPPORT = "support"
VERIFICATION = "verification"

STARTER_SETS: dict[str, tuple[str, ...]] = {
    # § 24.1.5: manages the staff and their sets, blocks and restores, reviews platform conflicts;
    # access to a company's data needs a reason. Finances, documents and files in privileged
    # sessions and the journal: PROPOSAL.
    PLATFORM_ADMIN: (
        "staff.manage",
        "journal.view",
        "journal.export",
        "directory.search",
        "contacts.reveal",
        "tickets.manage",
        "privileged.open",
        "privileged.finance",
        "privileged.documents",
        "privileged.files",
        "block.manage",
        "accounts.merge",
        "verification.resolve",
        "recovery.manage",
        "recovery.approve",
    ),
    # § 24.1.5: finds users and companies, keeps tickets, views what a ticket needs after a reason;
    # no business operations, no system roles. The reveal of contacts: PROPOSAL.
    # Block 11 П5 (PROPOSAL): the merge of accounts and the recovery requests.
    SUPPORT: (
        "directory.search",
        "contacts.reveal",
        "tickets.manage",
        "privileged.open",
        "accounts.merge",
        "recovery.manage",
    ),
    # § 24.1.5, § 24.1.7: reviews links and disputes, unlinks a disputed contact; no finances
    # and documents without a separate reason and permission. Tickets and contacts: PROPOSAL.
    # Block 11 П5 (PROPOSAL): the decisions on disputes and the confirmation of a recovery
    # («restoration after the check», § 24.1.7).
    VERIFICATION: (
        "directory.search",
        "contacts.reveal",
        "tickets.manage",
        "privileged.open",
        "verification.resolve",
        "recovery.manage",
        "recovery.approve",
    ),
}
# The deferrals of a notice (§ 24.2.2, § 24.3.2) are in no starter set: «only by a separate
# system permission» (TODO(spec): who holds them). A platform administrator gives them through a
# set of his own.

# Reason categories of a privileged session and of a block. PROPOSAL (TODO(spec): the lists).
SESSION_REASONS = ("support_ticket", "security_investigation", "verification", "other")
# § 24.2.2 names a violation and a threat to security.
BLOCK_CATEGORIES = ("violation", "security_threat")

# Areas of data of a privileged session and the permission each needs (§ 24.3.1).
SCOPES: dict[str, str | None] = {
    "view": None,
    "finance": "privileged.finance",
    "documents": "privileged.documents",
    "files": "privileged.files",
}
