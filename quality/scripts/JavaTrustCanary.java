import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

public class JavaTrustCanary {
    public static void main(String[] args) {
        if (args.length != 2) {
            System.err.println("Usage: JavaTrustCanary <keystore> <mock-certificate>");
            System.exit(2);
        }
        try {
            KeyStore store = KeyStore.getInstance(KeyStore.getDefaultType());
            try (InputStream input = Files.newInputStream(Path.of(args[0]))) {
                store.load(input, "changeit".toCharArray());
            }
            X509Certificate[] chain;
            try (InputStream input = Files.newInputStream(Path.of(args[1]))) {
                chain = CertificateFactory.getInstance("X.509").generateCertificates(input)
                        .toArray(new X509Certificate[0]);
            }
            TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            factory.init(store);
            for (TrustManager manager : factory.getTrustManagers()) {
                if (manager instanceof X509TrustManager) {
                    ((X509TrustManager) manager).checkServerTrusted(chain, chain[0].getPublicKey().getAlgorithm());
                    return;
                }
            }
            throw new IllegalStateException("No Java certificate trust manager");
        } catch (Exception failure) {
            System.err.println("Java truststore cannot validate the mock signing certificate");
            System.exit(1);
        }
    }
}
