using System.Runtime.InteropServices;
using System.Text;

namespace PolNotifier.Core;

/// <summary>Senha lembrada no Windows Credential Manager, no mesmo formato do keyring do Python
/// (WinVaultKeyring): credencial genérica com alvo = serviço e UserName = e-mail; se outro e-mail já
/// ocupa esse alvo, o keyring usa o alvo composto "e-mail@serviço". Blob em UTF-16LE.</summary>
public static class CredentialStore
{
    public const string Service = "POL ADV Notifier";
    const int CRED_TYPE_GENERIC = 1, CRED_PERSIST_ENTERPRISE = 3, ERROR_NOT_FOUND = 1168;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL
    {
        public int Flags, Type;
        public string TargetName;
        public string? Comment;
        public long LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist, AttributeCount;
        public IntPtr Attributes;
        public string? TargetAlias;
        public string UserName;
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredReadW(string target, int type, int flags, out IntPtr cred);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredWriteW(ref CREDENTIAL cred, int flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredDeleteW(string target, int type, int flags);
    [DllImport("advapi32.dll")]
    static extern void CredFree(IntPtr cred);

    static string Compound(string user, string service) => $"{user}@{service}";

    static (string User, string Password)? Read(string target)
    {
        if (!CredReadW(target, CRED_TYPE_GENERIC, 0, out var ptr)) return null;
        try
        {
            var c = Marshal.PtrToStructure<CREDENTIAL>(ptr);
            var bytes = new byte[c.CredentialBlobSize];
            Marshal.Copy(c.CredentialBlob, bytes, 0, bytes.Length);
            return (c.UserName ?? "", Encoding.Unicode.GetString(bytes));
        }
        finally { CredFree(ptr); }
    }

    static void Write(string target, string user, string password)
    {
        var bytes = Encoding.Unicode.GetBytes(password);
        var blob = Marshal.AllocHGlobal(Math.Max(1, bytes.Length));
        try
        {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            var c = new CREDENTIAL
            {
                Type = CRED_TYPE_GENERIC, TargetName = target, UserName = user, Comment = "Stored using python-keyring",
                CredentialBlob = blob, CredentialBlobSize = bytes.Length, Persist = CRED_PERSIST_ENTERPRISE,
            };
            if (!CredWriteW(ref c, 0)) throw new System.ComponentModel.Win32Exception();
        }
        finally { Marshal.FreeHGlobal(blob); }
    }

    public static string? GetPassword(string user, string service = Service)
    {
        var res = Read(service);
        if (res == null || res.Value.User != user) res = Read(Compound(user, service));
        return res?.Password;
    }

    public static void SetPassword(string user, string password, string service = Service)
    {
        // Igual ao keyring: quem estava no alvo simples vai para o alvo composto
        if (Read(service) is { } existing && existing.User != user) Write(Compound(existing.User, service), existing.User, existing.Password);
        Write(service, user, password);
    }

    public static void DeletePassword(string user, string service = Service)
    {
        foreach (var target in new[] { service, Compound(user, service) })
            if (Read(target) is { } c && c.User == user && !CredDeleteW(target, CRED_TYPE_GENERIC, 0)
                && Marshal.GetLastWin32Error() != ERROR_NOT_FOUND)
                throw new System.ComponentModel.Win32Exception();
    }
}
