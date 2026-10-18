"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.expandHome = expandHome;
exports.isAllowed = isAllowed;
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
function expandHome(p) {
    return p === '~' || p.startsWith('~/') || p.startsWith('~\\') ? path.join(os.homedir(), p.slice(1)) : p;
}
function real(p) {
    try {
        return fs.realpathSync(p);
    }
    catch {
        return path.resolve(p);
    }
}
/** True only for files inside lesson/{topic}/starter, and never for obvious secret files. */
function isAllowed(file, folders) {
    const base = path.basename(file).toLowerCase();
    if (base.startsWith('.env') || base.endsWith('.pem') || base.endsWith('.key')) {
        return false;
    }
    const f = real(file);
    const normalized = f.replace(/\\/g, '/');
    // Must be inside a starter/ subfolder of a lesson topic
    const isStarterFile = normalized.includes('/starter/') || normalized.endsWith('/starter');
    if (!isStarterFile) {
        return false;
    }
    return folders.some((folder) => {
        const rel = path.relative(real(expandHome(folder)), f);
        return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
    });
}
//# sourceMappingURL=allowlist.js.map