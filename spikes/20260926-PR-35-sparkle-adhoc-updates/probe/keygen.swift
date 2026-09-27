// Makes a throwaway EdDSA (Ed25519) key pair for the experiment, as files.
//
// Sparkle's own generate_keys keeps the private key in the login Keychain;
// this avoids leaving anything there. sign_update --ed-key-file reads the
// private key as the base64 of the 32-byte seed, and SUPublicEDKey is the
// base64 of the 32-byte public key.
//
//   swift keygen.swift <private-key-file> <public-key-file>

import CryptoKit
import Foundation

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write(Data("usage: keygen.swift <private-key-file> <public-key-file>\n".utf8))
    exit(2)
}
let key = Curve25519.Signing.PrivateKey()
try key.rawRepresentation.base64EncodedString().write(toFile: args[1], atomically: true, encoding: .utf8)
try key.publicKey.rawRepresentation.base64EncodedString().write(toFile: args[2], atomically: true, encoding: .utf8)
