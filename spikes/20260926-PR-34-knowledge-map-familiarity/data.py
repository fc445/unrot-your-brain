"""Constructed concepts and personas. Nothing here was observed; it is all authored.

Depth is within an area:
  1 = the name anyone near the area has heard ("Postgres")
  2 = what a working practitioner uses day to day ("database index")
  3 = internals you meet once you go deeper ("MVCC")
  4 = specialist ("serializable snapshot isolation")

A persona knows a concept iff depth <= persona[area]. That rule is the ground
truth, and it is exactly the area-vs-depth structure the spike is probing -- so
the labels are synthetic and flatter the question in the methods' favour: real
knowledge is lumpier than a clean depth cut-off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Concept:
    term: str
    gloss: str
    area: str
    depth: int

    @property
    def text(self) -> str:
        return f"{self.term}: {self.gloss}"


_RAW: dict[str, dict[int, list[tuple[str, str]]]] = {
    "databases": {
        1: [
            ("SQL", "the query language used to read and write relational databases"),
            ("Postgres", "a popular open-source relational database"),
            ("database table", "rows and columns holding one kind of record"),
            ("primary key", "the column that uniquely identifies each row"),
        ],
        2: [
            ("database index", "a lookup structure that makes queries on a column fast"),
            ("JOIN", "combining rows from two tables on a matching column"),
            ("transaction", "a group of writes that succeed or fail together"),
            ("ORM", "a library that maps database rows to objects in code"),
        ],
        3: [
            ("MVCC", "multi-version concurrency control: keeping several versions of a row so readers don't block writers"),
            ("write-ahead log", "a log of changes written before the data pages, so a crash can be recovered"),
            ("query planner", "the part of the database that chooses how to execute a query"),
            ("isolation levels", "settings that decide which concurrent changes a transaction can see"),
        ],
        4: [
            ("serializable snapshot isolation", "detecting dangerous read-write dependencies between snapshot transactions to guarantee serializability"),
            ("LSM tree", "a log-structured merge tree that batches writes into sorted runs and compacts them"),
            ("write skew", "an anomaly where two transactions read overlapping data and write disjoint rows, breaking an invariant"),
        ],
    },
    "networking": {
        1: [
            ("IP address", "the numeric address of a device on a network"),
            ("DNS", "the system that turns domain names into IP addresses"),
            ("HTTP", "the protocol browsers use to request web pages"),
            ("URL", "the address of a resource on the web"),
        ],
        2: [
            ("TCP", "a protocol that delivers a reliable, ordered stream of bytes"),
            ("HTTPS", "HTTP encrypted with TLS"),
            ("port number", "a number that identifies which service on a machine a connection is for"),
            ("load balancer", "something that spreads incoming requests across several servers"),
        ],
        3: [
            ("CIDR notation", "writing an IP range as an address plus prefix length, like 10.0.0.0/16"),
            ("NAT", "network address translation: rewriting addresses so many devices share one public IP"),
            ("TLS handshake", "the exchange that agrees keys and verifies certificates before encrypted traffic starts"),
            ("HTTP/2 multiplexing", "sending many requests interleaved over one connection"),
        ],
        4: [
            ("BGP", "the routing protocol networks use to announce which IP ranges they can reach"),
            ("QUIC", "a transport protocol over UDP with built-in encryption and no head-of-line blocking"),
            ("Nagle's algorithm", "delaying small TCP writes so they can be combined into fewer packets"),
        ],
    },
    "frontend": {
        1: [
            ("HTML", "the markup language that structures a web page"),
            ("CSS", "the language that styles a web page"),
            ("JavaScript", "the programming language that runs in web browsers"),
            ("web browser", "the app used to view websites"),
        ],
        2: [
            ("DOM", "the tree of elements the browser builds from a page, which scripts can change"),
            ("React", "a JavaScript library for building user interfaces from components"),
            ("flexbox", "a CSS layout mode for arranging items in a row or column"),
            ("responsive design", "making a page adapt its layout to different screen sizes"),
        ],
        3: [
            ("virtual DOM reconciliation", "diffing a new UI tree against the old one to apply minimal DOM changes"),
            ("event delegation", "handling events for many children with one listener on a parent"),
            ("CORS", "browser rules that decide when a page may call an API on another origin"),
            ("hydration", "attaching client-side behaviour to HTML that was rendered on the server"),
        ],
        4: [
            ("layout thrashing", "forcing repeated synchronous layout by interleaving DOM reads and writes"),
            ("React Fiber", "React's internal scheduler that splits rendering into interruptible units of work"),
            ("critical rendering path", "the sequence of steps from bytes to pixels that gates first paint"),
        ],
    },
    "infra": {
        1: [
            ("server", "a computer that provides a service to other computers"),
            ("cloud computing", "renting computing resources from a provider over the internet"),
            ("environment variable", "a named setting passed to a program by its environment"),
            ("log file", "a file where a program records what it did"),
        ],
        2: [
            ("Docker", "a tool for packaging and running applications in containers"),
            ("CI/CD pipeline", "automation that tests and deploys code on every change"),
            ("SSH", "a secure way to log in to and run commands on a remote machine"),
            ("virtual machine", "an emulated computer running on shared physical hardware"),
        ],
        3: [
            ("Kubernetes", "a system that schedules and manages containers across a cluster"),
            ("Terraform", "infrastructure as code: declaring cloud resources in files and applying them"),
            ("blue-green deployment", "switching traffic between two identical environments to release safely"),
            ("reverse proxy", "a server in front of backends that forwards client requests to them"),
        ],
        4: [
            ("service mesh", "a sidecar proxy layer that handles traffic, retries and mTLS between services"),
            ("eBPF", "running sandboxed programs inside the Linux kernel for tracing and networking"),
            ("cgroups", "the Linux kernel feature that limits and accounts resource use of process groups"),
        ],
    },
    "ml": {
        1: [
            ("machine learning", "computers learning patterns from data rather than following explicit rules"),
            ("neural network", "a model made of layers of connected artificial neurons"),
            ("training data", "the examples a model learns from"),
            ("classification", "predicting which category something belongs to"),
        ],
        2: [
            ("overfitting", "a model memorising its training data and doing badly on new data"),
            ("gradient descent", "adjusting parameters step by step in the direction that reduces error"),
            ("loss function", "a number measuring how wrong a model's predictions are"),
            ("train/test split", "holding back some data to check how a model does on unseen examples"),
        ],
        3: [
            ("backpropagation", "computing each weight's gradient by applying the chain rule backwards through the network"),
            ("attention mechanism", "letting each position in a sequence weigh information from other positions"),
            ("embedding vector", "a learned list of numbers representing an item so similar items are close"),
            ("L2 regularisation", "penalising large weights to reduce overfitting"),
        ],
        4: [
            ("KV cache", "storing attention keys and values from earlier tokens to speed up generation"),
            ("LoRA", "fine-tuning a large model by training small low-rank update matrices"),
            ("mixture of experts", "routing each token to a few specialist sub-networks out of many"),
        ],
    },
    "security": {
        1: [
            ("password", "a secret word used to log in"),
            ("encryption", "scrambling data so only someone with the key can read it"),
            ("two-factor authentication", "logging in with a password plus a second proof, like a phone code"),
            ("phishing", "tricking people into giving away credentials with fake messages"),
        ],
        2: [
            ("hashing", "turning data into a fixed-size fingerprint that can't be reversed"),
            ("SQL injection", "attacking a database by smuggling SQL into user input"),
            ("OAuth", "a standard for letting an app act on your behalf without your password"),
            ("API key", "a secret token that identifies a program calling an API"),
        ],
        3: [
            ("XSS", "cross-site scripting: injecting script into a page other users will load"),
            ("CSRF", "cross-site request forgery: making a logged-in browser send an unwanted request"),
            ("password salting", "adding random data to each password before hashing so identical passwords differ"),
            ("JWT", "a signed JSON token carrying claims, used for stateless authentication"),
        ],
        4: [
            ("timing attack", "learning secrets from how long an operation takes"),
            ("padding oracle attack", "decrypting ciphertext by observing whether the server reports padding errors"),
            ("return-oriented programming", "chaining existing code fragments ending in return to execute an exploit"),
        ],
    },
    "languages": {
        1: [
            ("variable", "a named place that holds a value in a program"),
            ("function", "a reusable block of code that takes inputs and returns a result"),
            ("loop", "code that repeats until a condition is met"),
            ("if statement", "code that runs only when a condition is true"),
        ],
        2: [
            ("class", "a template for creating objects with data and methods"),
            ("recursion", "a function that calls itself on a smaller version of the problem"),
            ("exception handling", "catching and responding to errors when they are raised"),
            ("hash map", "a structure that stores values by key with fast lookup"),
        ],
        3: [
            ("closure", "a function that keeps access to variables from the scope it was created in"),
            ("generics", "writing code that works over many types while staying type-safe"),
            ("async/await", "syntax for writing asynchronous code that reads like sequential code"),
            ("higher-order function", "a function that takes or returns other functions"),
        ],
        4: [
            ("monad", "a pattern for chaining computations that carry context, like failure or state"),
            ("higher-kinded types", "types that are parameterised by other type constructors"),
            ("continuation-passing style", "writing functions that pass their result to an explicit next-step function"),
        ],
    },
    "os": {
        1: [
            ("operating system", "the software that manages a computer's hardware and runs programs"),
            ("file system", "how files and folders are organised and stored on disk"),
            ("RAM", "the computer's fast, temporary working memory"),
            ("CPU", "the processor that executes instructions"),
        ],
        2: [
            ("process", "a running instance of a program with its own memory"),
            ("thread", "a sequence of execution inside a process that shares its memory"),
            ("shell", "a command-line program for running other programs"),
            ("file permissions", "rules for who may read, write or execute a file"),
        ],
        3: [
            ("mutex", "a lock that lets only one thread into a section of code at a time"),
            ("deadlock", "threads each waiting for a lock another holds, so none can proceed"),
            ("virtual memory", "giving each process its own address space mapped onto physical memory"),
            ("system call", "a request from a program to the kernel to do something privileged"),
        ],
        4: [
            ("TLB", "translation lookaside buffer: a CPU cache of recent virtual-to-physical address mappings"),
            ("futex", "a fast userspace mutex that only enters the kernel when there is contention"),
            ("memory barrier", "an instruction that stops the CPU reordering memory operations across it"),
        ],
    },
    "distributed": {
        1: [
            ("API", "a defined way for one program to ask another for data or actions"),
            ("cache", "a fast store of recent results so they don't have to be recomputed"),
            ("client-server model", "clients sending requests to a central server that responds"),
            ("microservices", "building an application as many small independently deployed services"),
        ],
        2: [
            ("message queue", "a buffer that lets services send each other work asynchronously"),
            ("rate limiting", "capping how many requests a client can make in a period"),
            ("replication", "keeping copies of data on several machines"),
            ("idempotency", "an operation that has the same effect however many times it is repeated"),
        ],
        3: [
            ("CAP theorem", "a distributed store can't guarantee consistency, availability and partition tolerance at once"),
            ("eventual consistency", "replicas may disagree briefly but converge if writes stop"),
            ("consensus algorithm", "a way for machines to agree on one value despite failures"),
            ("sharding", "splitting data across machines by key"),
        ],
        4: [
            ("Raft", "a consensus algorithm built around an elected leader and a replicated log"),
            ("vector clock", "per-node counters that capture causal order between events"),
            ("CRDT", "a data type whose replicas can be merged without conflicts"),
        ],
    },
    "tooling": {
        1: [
            ("git", "a version control system for tracking changes to code"),
            ("code editor", "an app for writing and editing source code"),
            ("commit", "a saved snapshot of changes in version control"),
            ("GitHub", "a website that hosts git repositories and code collaboration"),
        ],
        2: [
            ("branch", "an independent line of development in a repository"),
            ("merge conflict", "when two changes touch the same lines and git can't combine them"),
            ("pull request", "a proposal to merge a branch, with review and discussion"),
            ("package manager", "a tool that installs and updates a project's dependencies"),
        ],
        3: [
            ("rebase", "replaying commits on top of another base to rewrite history linearly"),
            ("cherry-pick", "applying one specific commit from another branch"),
            ("lockfile", "a file pinning the exact dependency versions that were installed"),
            ("git bisect", "binary-searching history to find the commit that introduced a bug"),
        ],
        4: [
            ("reflog", "git's record of where branch tips pointed, used to recover lost commits"),
            ("packfile", "git's compressed storage format holding objects as deltas"),
            ("Bazel", "a build system that models the whole repo as a graph of cached, hermetic targets"),
        ],
    },
    "cooking": {
        1: [
            ("boil", "cook in water at a rolling bubble"),
            ("fry", "cook in hot oil or fat"),
            ("bake", "cook with dry heat in an oven"),
            ("chop", "cut food into pieces with a knife"),
            ("season", "add salt, pepper or spices for flavour"),
        ],
        2: [
            ("sauté", "cook quickly in a little fat over high heat, moving the food"),
            ("simmer", "cook in liquid just below boiling"),
            ("roux", "flour cooked in fat, used to thicken sauces"),
            ("marinade", "a seasoned liquid food is soaked in before cooking"),
            ("blanch", "briefly boil then cool in iced water"),
        ],
        3: [
            ("emulsion", "a stable mix of two liquids that normally separate, like oil and vinegar"),
            ("Maillard reaction", "browning of proteins and sugars under heat that creates savoury flavour"),
            ("deglaze", "loosening browned bits from a pan with liquid to make a sauce"),
            ("sourdough starter", "a live culture of wild yeast and bacteria used to leaven bread"),
            ("braise", "brown then cook slowly in a little liquid in a covered pot"),
        ],
        4: [
            ("spherification", "forming liquid into gel-skinned spheres with sodium alginate and calcium"),
            ("lamination", "folding butter into dough in many thin layers, as for croissants"),
            ("nixtamalization", "cooking corn in an alkaline solution to make masa"),
            ("autolyse", "resting flour and water before adding salt and yeast to develop gluten"),
            ("transglutaminase", "an enzyme used to bind proteins together, sometimes called meat glue"),
        ],
    },
}

CONCEPTS: list[Concept] = [
    Concept(term, gloss, area, depth)
    for area, by_depth in _RAW.items()
    for depth, items in by_depth.items()
    for term, gloss in items
]

AREAS = list(_RAW)

#: area -> deepest level known. A missing area means 0: knows nothing there.
PERSONAS: dict[str, dict[str, int]] = {
    # Knows Postgres and indexes, not MVCC: the case the spike is really about.
    "backend": dict(databases=2, networking=3, frontend=1, infra=3, ml=1, security=3,
                    languages=3, os=2, distributed=3, tooling=3, cooking=1),
    "frontend": dict(databases=1, networking=2, frontend=4, infra=1, ml=0, security=2,
                     languages=3, os=1, distributed=1, tooling=3, cooking=2),
    "junior": dict(databases=1, networking=1, frontend=2, infra=1, ml=1, security=1,
                   languages=2, os=1, distributed=1, tooling=2, cooking=1),
    "ml_engineer": dict(databases=2, networking=1, frontend=0, infra=2, ml=4, security=1,
                        languages=3, os=1, distributed=2, tooling=2, cooking=1),
    # The cross-domain case: expert in one domain, light exposure to the other.
    "hobby_cook": dict(databases=0, networking=1, frontend=1, infra=0, ml=1, security=1,
                       languages=0, os=1, distributed=0, tooling=0, cooking=4),
}


def knows(persona: str, concept: Concept) -> bool:
    return concept.depth <= PERSONAS[persona].get(concept.area, 0)
